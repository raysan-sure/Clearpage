"""Synthetic tests for image ingestion and color correction plus cleanup."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageDraw
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
import app
from clearpage.pdf import engine
from clearpage.pdf import scan_input
from tests.test_quality import text_page, pdf


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.client=app.app.test_client()
        with self.client.get('/'):pass
        self.headers={'X-ClearPage':'local'}

    def tearDown(self):
        for job in list(app.JOBS.values()):
            job['state']='ready';self.client.post('/api/clear/'+job['id'],json={},headers=self.headers)
        self.tmp.cleanup()

    def upload(self,data,name):
        response=self.client.post('/api/upload',data=data,headers=dict(self.headers,**{'X-Filename':name}))
        self.assertEqual(response.status_code,200,response.json)
        return app.JOBS[response.json['id']]

    def batch(self,job,options):
        options=app.settings(options);app.prepare_options(job,options)
        job.update(state='processing',results=[],options=options,cancel=False)
        app.batch(job,options);self.assertEqual(job['state'],'review',job['message'])

    def test_png_transparency_and_jpeg_exif_orientation(self):
        source=self.root/'transparent.png';target=self.root/'image.pdf'
        image=Image.new('RGBA',(120,80),(0,0,0,0));ImageDraw.Draw(image).rectangle((20,20,40,40),fill=(0,0,0,255))
        image.save(source,dpi=(144,144));scan_input.import_scan(source,target)
        rendered=np.array(engine.render_page(target,0))
        self.assertTrue(np.all(rendered[0,0]==255));self.assertTrue(np.all(rendered[30,30]==0))
        image=Image.new('RGB',(120,80),'white');ImageDraw.Draw(image).rectangle((0,0,40,30),fill='black')
        exif=Image.Exif();exif[274]=6
        source=self.root/'rotated.jpg';image.save(source,exif=exif,dpi=(144,144),quality=95)
        scan_input.import_scan(source,target)
        rendered=engine.render_page(target,0)
        self.assertEqual(rendered.size,(80,120));self.assertLess(np.array(rendered)[10,70].mean(),10)

    def test_tiff_pages_dpi_and_original_pixel_embedding(self):
        source=self.root/'scans.tiff';target=self.root/'image.pdf'
        images=[text_page(True),text_page(),Image.new('RGB',(600,800),(25,70,120))]
        images[0].save(source,save_all=True,append_images=images[1:],dpi=(144,144),compression='tiff_deflate')
        data=source.read_bytes();info=scan_input.import_scan(source,target)
        self.assertEqual(info['input_type'],'TIFF');self.assertEqual(source.read_bytes(),data)
        reader=PdfReader(target);self.assertEqual(len(reader.pages),3)
        for i,image in enumerate(images):
            self.assertTrue(np.array_equal(image,engine.render_page(target,i)))
            self.assertEqual(float(reader.pages[i].mediabox.width),300)
            self.assertTrue(np.array_equal(image,reader.pages[i].images[0].image.convert('RGB')))

    def test_png_fractional_dpi_does_not_resample_full_page_correction(self):
        memory=BytesIO();text_page(True).save(memory,format='PNG',dpi=(144,144))
        job=self.upload(memory.getvalue(),'negative.png')
        self.assertEqual(float(PdfReader(job['source']).pages[0].mediabox.width),300)
        self.batch(job,dict(method='polarity'))
        app.export_worker(job,[0]);self.assertEqual(job['state'],'done')
        self.assertTrue(np.array_equal(text_page(),engine.render_page(job['dir']/'cleaned.pdf',0)))

    def test_invalid_images_limits_and_cleanup(self):
        invalid=self.client.post('/api/upload',data=b'not a scan',headers=self.headers)
        self.assertEqual(invalid.status_code,400);self.assertFalse(app.JOBS)
        source=self.root/'image.gif';Image.new('RGB',(20,20),'white').save(source)
        with self.assertRaises(ValueError):scan_input.import_scan(source,self.root/'bad.pdf')
        source=self.root/'image.png';Image.new('RGB',(100,100),'white').save(source)
        with patch.object(scan_input,'MAX_PIXELS',500):
            with self.assertRaises(ValueError):scan_input.import_scan(source,self.root/'bad.pdf')
        self.assertFalse((self.root/'bad.part').exists());self.assertFalse((self.root/'bad.pdf').exists())
        source=self.root/'multi.tif';im=Image.new('RGB',(100,100),'white');im.save(source,save_all=True,append_images=[im],dpi=(144,144))
        with patch.object(scan_input,'MAX_TOTAL_PIXELS',15000):
            with self.assertRaises(ValueError):scan_input.import_scan(source,self.root/'bad.pdf')
        self.assertFalse((self.root/'bad.part').exists())

    def test_combined_manual_cleanup_uses_normalized_pixels_and_exports_all_pages(self):
        positive=text_page();ImageDraw.Draw(positive).ellipse((200,690,230,720),fill=(195,195,195))
        negative=Image.fromarray(255-np.array(positive))
        memory=BytesIO();negative.save(memory,format='TIFF',save_all=True,append_images=[text_page()],dpi=(144,144))
        job=self.upload(memory.getvalue(),'mixed.tiff')
        strokes=[dict(points=[[215/600,705/800]],width=.1)]
        options=dict(method='manual',mode='light',fix_negative=True,strokes=strokes,auto_restore=False,use_reviewed=False)
        self.batch(job,options)
        self.assertTrue(job['results'][0]['color_correction']['applied']);self.assertTrue(job['results'][0]['uncertain'])
        source=np.array(Image.open(app.original(job,0)))
        expected,stats=engine.clean(Image.fromarray(255-source),engine.paint_mask(positive.size,strokes),'light')
        self.assertGreater(stats['changed'],0)
        self.assertTrue(np.array_equal(expected,Image.open(app.image_path(job,0,True))))
        app.export_worker(job,[0]);self.assertEqual(job['state'],'done')
        self.assertTrue(np.array_equal(expected,engine.render_page(job['dir']/'cleaned.pdf',0)))
        self.assertTrue(np.array_equal(engine.render_page(job['source'],1),engine.render_page(job['dir']/'cleaned.pdf',1)))
        self.batch(job,options)
        self.assertTrue(np.array_equal(expected,Image.open(app.image_path(job,0,True))))
        # OCR receives the normalized page and a separate cache; it never reads stale negative OCR.
        paths=[]
        def ocr(path,cache):paths.append((Path(path).name,Path(cache).name));return []
        with patch.object(app,'local_ocr',side_effect=ocr):app.restoration_worker(job)
        self.assertTrue(paths);self.assertTrue(all('normalized' in name for name,cache in paths))
        # Accepted patches cannot be silently reused after switching page polarity.
        job['repairs']={0:[dict(box=(0,0,1,1),patch=Image.new('RGB',(1,1),'white'))]}
        previous=Image.open(app.image_path(job,0,True)).tobytes()
        result=self.client.post('/api/preview/'+job['id'],json=options|dict(fix_negative=False),headers=self.headers)
        self.assertEqual(result.status_code,400)
        self.assertEqual(previous,Image.open(app.image_path(job,0,True)).tobytes())

    def test_normalization_reaches_preset_lama_and_purescan_and_invalidates_model(self):
        job=self.upload(pdf([text_page(True)]),'negative.pdf')
        source=np.array(engine.render_page(job['source'],0));normal=255-source
        mask=np.zeros(source.shape[:2],bool);mask[700:710,200:210]=True
        seen=[]
        def known(image,*args,**kwargs):seen.append(np.array(image));return mask,1.
        def lama(image,mask,*args,**kwargs):self.assertTrue(np.array_equal(image,normal));return image.copy()
        with patch.object(app,'known_mask',side_effect=known),patch.object(app.neural_restore,'inpaint',side_effect=lama):
            self.batch(job,dict(method='preset',mode='lama',fix_negative=True,use_reviewed=False))
        self.assertTrue(np.array_equal(seen[0],normal))
        captured=[]
        def learn(pages,*args):captured.extend(np.array(image) for i,image in pages);return {'ok':True}
        def apply(image,current,*args):self.assertTrue(np.array_equal(image,normal));return current,{}
        with patch.object(app,'known_mask',side_effect=known),patch.object(app.scan_inversion,'learn_template',side_effect=learn),patch.object(app.scan_inversion,'apply',side_effect=apply):
            self.batch(job,dict(method='preset',fix_negative=True,scan_inversion=True,use_reviewed=False))
        self.assertTrue(np.array_equal(captured[0],normal))
        app.prepare_options(job,app.settings(dict(method='preset',fix_negative=False)))
        self.assertNotIn('inversion_model',job)

    def test_combined_structural_overlay_export_and_unselected_original(self):
        memory=BytesIO();c=Canvas(memory,pagesize=(300,400))
        for _ in range(2):
            c.drawImage(ImageReader(text_page(True)),0,0,300,400)
            for y,text in ((300,'UNOFFICIAL COPY'),(200,"Property of Cook County Clerk's Office")):
                c._code.append(f'q 1 0 0 1 5 {y} cm BT /F1 12 Tf .8 .8 .8 rg ({text}) Tj ET Q')
            c.showPage()
        c.save();job=self.upload(memory.getvalue(),'negative-overlay.pdf')
        self.batch(job,dict(method='overlay',fix_negative=True))
        self.assertEqual(job['results'][0]['overlays'],2)
        self.assertTrue(job['results'][0]['color_correction']['applied'])
        app.export_worker(job,[0])
        self.assertEqual(job['state'],'done')
        self.assertTrue(np.array_equal(Image.open(app.image_path(job,0,True)),engine.render_page(job['dir']/'cleaned.pdf',0)))
        self.assertTrue(np.array_equal(engine.render_page(job['source'],1),engine.render_page(job['dir']/'cleaned.pdf',1)))


if __name__=='__main__':unittest.main(verbosity=2)
