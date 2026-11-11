import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
from pypdf.generic import NameObject,FloatObject
import app, benchmark
from clearpage.pdf import engine, pdf_selection, pdf_overlays, quality_check
from clearpage import saved_work
from tests.test_apdf import sample


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.pdf';self.target=self.root/'result.pdf'
        self.client=app.app.test_client();self.client.get('/')
        self.headers={'X-ClearPage':'local'};self.jobs=[]
    def tearDown(self):
        for identifier in self.jobs:self.client.post('/api/clear/'+identifier,json={},headers=self.headers)
        self.tmp.cleanup()
    def post(self,path,data):
        response=self.client.post(path,json=data,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json);return response.json
    def upload(self):
        response=self.client.post('/api/upload',data=self.source.read_bytes(),headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        self.jobs.append(response.json['id']);return app.JOBS[response.json['id']]
    def test_variants_keep_original_body(self):
        def mutate(mark,font,state):
            font[NameObject('/BaseFont')]=NameObject('/Times-Bold')
            state[NameObject('/ca')]=FloatObject(.3);state[NameObject('/CA')]=FloatObject(.3)
            return mark.replace(b'(Sample)',b'(DRAFT)').replace(b'103.1638 Tf',b'55 Tf').replace(b'0 0 1 rg',b'.2 .4 .2 rg')
        sample(self.source,mutation=mutate)
        self.assertEqual(pdf_overlays.inspect(self.source)[0]['providers'],['A-PDF text variant'])
        pdf_overlays.write_clean(self.source,self.target,[0])
        self.assertEqual(pdf_overlays.read_pdf(self.target).pages[0].extract_text(),'Body: Sample and www.apdf.com 12345')
        self.assertTrue(np.array_equal(engine.render_page(self.source,1),engine.render_page(self.target,1)))
    def test_selection_shared_form_and_stale_id(self):
        sample(self.source,nested=True,mutation=lambda m,f,s:m.replace(b'(Sample)',b'(PRIVATE MARK)'))
        reader=pdf_selection.read_pdf(self.source)
        selected=next(x for x in pdf_selection.inspect_page(reader,0) if x['text']=='PRIVATE MARK')
        pdf_selection.write_selected(self.source,self.target,[0],{'0':[selected['id']]})
        self.assertNotIn('PRIVATE MARK',pdf_selection.read_pdf(self.target).pages[0].extract_text())
        self.assertTrue(np.array_equal(engine.render_page(self.source,1),engine.render_page(self.target,1)))
        before=self.target.read_bytes()
        with self.assertRaises(ValueError):pdf_selection.write_selected(self.source,self.target,[0],{'0':['bad']})
        self.assertEqual(self.target.read_bytes(),before)
    def test_live_routes_review_export_and_resume(self):
        sample(self.source,mutation=lambda m,f,s:m.replace(b'(Sample)',b'(MY WATERMARK)'))
        job=self.upload();identifier=job['id']
        candidates=self.post('/api/objects/'+identifier+'/0',{})['items']
        candidate=next(c for c in candidates if c['text']=='MY WATERMARK')
        self.assertTrue(all(0<=v<=1 for v in candidate['box']))
        chosen=self.post('/api/objects/'+identifier+'/select',{'page':0,'id':candidate['id'],'all_pages':True})
        self.assertEqual(chosen['pages'],[1,2]);self.assertEqual(job['options']['method'],'object')
        app.batch(job,job['options']);self.assertEqual(job['state'],'review')
        self.assertTrue(all(r['uncertain'] for r in job['results']))
        app.export_worker(job,[0]);self.assertEqual(job['state'],'done')
        self.assertNotIn('MY WATERMARK',pdf_selection.read_pdf(job['dir']/'cleaned.pdf').pages[0].extract_text())
        self.assertTrue(np.array_equal(engine.render_page(self.source,1),engine.render_page(job['dir']/'cleaned.pdf',1)))
        store=self.root/'saved';saved_work.save(store,'a'*24,[job])
        restore=self.root/'restore';restore.mkdir();loaded=saved_work.load(store,'a'*24,restore)
        self.assertEqual(loaded[0]['object_selection'],job['object_selection'])
        self.post('/api/objects/'+identifier+'/clear',{});self.assertFalse(job.get('object_selection'))
        self.assertFalse((job['dir']/'cleaned.pdf').exists())
    def test_faint_connected_ink_survives_scan_cleanup(self):
        source=Image.new('RGB',(200,100),'white');draw=ImageDraw.Draw(source)
        draw.line((10,50,190,50),fill=(210,210,210),width=3)
        mask=np.zeros((100,200),bool);mask[25:75,85:115]=True
        result,stats=engine.clean(source,mask,'careful')
        self.assertTrue(np.array_equal(result[49:52],np.array(source)[49:52]))
        self.assertTrue(stats['scan_overlap_regions'])
        self.assertTrue(np.array_equal(result[~mask],np.array(source)[~mask]))
    def test_benchmark_rejects_missing_or_damaged_cases(self):
        reference=Image.new('RGB',(50,50),'white');ImageDraw.Draw(reference).line((5,20,45,20),fill=(200,200,200),width=3)
        metrics=quality_check.compare(reference,reference,reference)
        self.assertEqual(metrics['faint_ink_pixel_match_percent'],100)
        baseline={'pages':[dict(case='faint',category='scan',page=1,**metrics)]}
        self.assertFalse(benchmark.regressions(baseline,baseline))
        self.assertTrue(benchmark.regressions({'pages':[]},baseline))
        damaged=quality_check.compare(reference,Image.new('RGB',(50,50),'white'),reference)
        self.assertTrue(benchmark.regressions({'pages':[dict(case='faint',category='scan',page=1,**damaged)]},baseline))


if __name__=='__main__':unittest.main()
