"""Synthetic preservation, cancellation, and actual upload-to-export regressions."""
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from clearpage.presets import red_scans
import app
from clearpage.pdf import engine


def fixture():
    h,w=800,600
    truth=np.full((h,w,3),255,'uint8')
    for y in range(130,680,35): truth[y:y+3,90:520]=30
    truth[90:92,80:540]=120
    yy,xx=np.ogrid[:h,:w]
    mark=(abs(yy-(h-xx*h/w))<18)&((xx//24)%2==0)
    alpha=mark[:,:,None]*.25
    a=np.rint(truth*(1-alpha)+np.array([255,0,0])*alpha).astype('uint8')
    return a,truth,mark


class RedScanTests(unittest.TestCase):
    def test_faint_neutral_edges_next_to_watermark_remain_exact(self):
        a,_,_=fixture()
        # Neutral pixels inside the old nine-pixel expansion must not whiten.
        for j,value in enumerate((165,180,200,220,240,250,254)):
            a[400+j,294:298]=value
        original=a.copy();result,_,stats=red_scans.clean(Image.fromarray(a))
        neutral=np.ptp(a,axis=2)==0
        self.assertTrue(stats['matched'])
        np.testing.assert_array_equal(result[neutral],original[neutral])
        np.testing.assert_array_equal(a,original)

    def test_faint_overlay_text_and_antialiasing_are_not_contrast_whitened(self):
        _,truth,mark=fixture()
        for y in range(130,680,35):
            truth[y:y+2,90:520]=195
            truth[y+2:y+3,90:520]=230
        observed=np.rint(truth*(1-mark[:,:,None]*.25)+np.array([255,0,0])*(mark[:,:,None]*.25)).astype('uint8')
        result,_,stats=red_scans.clean(Image.fromarray(observed))
        ink=(truth[:,:,0]<255)&mark
        self.assertTrue(stats['matched'])
        self.assertLessEqual(np.abs(result.astype(int)-truth.astype(int))[ink].max(),2)
        self.assertTrue((result[mark & (truth[:,:,0]==255)]==255).all())

    def test_inverse_recovers_dark_ink_and_preserves_unselected_pixels(self):
        a,truth,mark=fixture()
        result,changed,stats=red_scans.clean(Image.fromarray(a))
        self.assertTrue(stats['matched']);self.assertTrue(stats['uncertain'])
        self.assertFalse(stats['ai'])
        self.assertLessEqual(np.abs(result.astype(int)-truth.astype(int))[mark].max(),2)
        np.testing.assert_array_equal(result[~mark],truth[~mark])
        np.testing.assert_array_equal(changed,np.any(a!=result,2))

    def test_clean_blue_small_red_stamp_and_heading_are_unchanged(self):
        a,truth,_=fixture()
        stamp=truth.copy();stamp[600:650,50:100]=(255,180,180)
        heading=truth.copy();heading[30:60,30:550]=(255,180,180)
        for pixels in (truth,stamp,heading,a[:,:,[2,1,0]]):
            result,changed,stats=red_scans.clean(Image.fromarray(pixels))
            np.testing.assert_array_equal(result,pixels)
            self.assertFalse(changed.any());self.assertFalse(stats['matched'])

    def test_separate_red_seal_is_preserved(self):
        a,_,_=fixture();a[650:675,500:525]=(255,180,180)
        result,_,stats=red_scans.clean(Image.fromarray(a))
        self.assertTrue(stats['matched'])
        np.testing.assert_array_equal(result[650:675,500:525],a[650:675,500:525])

    def test_cancel_and_size_limit(self):
        with self.assertRaises(InterruptedError):red_scans.clean(Image.new('RGB',(10,10)),lambda:True)
        with patch.object(red_scans,'MAX_PIXELS',1):
            with self.assertRaises(ValueError):red_scans.clean(Image.new('RGB',(10,10)))

    def test_upload_preview_batch_native_and_rotated_export(self):
        client=app.app.test_client()
        with client.get('/'): pass
        headers={'X-ClearPage':'local'}
        with tempfile.TemporaryDirectory() as directory:
            a,_,_=fixture();buffer=BytesIO();canvas=Canvas(buffer,pagesize=(300,400))
            for _ in range(3):
                canvas.drawImage(ImageReader(Image.fromarray(a)),0,0,300,400);canvas.showPage()
            canvas.save();reader=PdfReader(buffer);reader.pages[2].rotate(90)
            writer=PdfWriter()
            for page in reader.pages: writer.add_page(page)
            source=Path(directory)/'source.pdf';writer.write(source);original=source.read_bytes()
            self.assertEqual(PdfReader(source).pages[2].rotation,90)
            response=client.post('/api/upload',data=original,headers=headers)
            self.assertEqual(response.status_code,200,response.json)
            key=response.json['id'];job=app.JOBS[key]
            try:
                preview=client.post('/api/preview/'+key,json={'method':'red_copy','page':0},headers=headers)
                self.assertEqual(preview.status_code,200,preview.json)
                self.assertTrue(preview.json['native_red']);self.assertTrue(preview.json['matched'])
                options=app.settings({'method':'red_copy','mode':'lama','auto_restore':True})
                self.assertEqual(options['mode'],'careful');self.assertFalse(options['auto_restore'])
                job.update(options=options,cancel=False,results=[])
                app.batch(job,options);self.assertEqual(job['state'],'review',job['message'])
                self.assertTrue(all(r['matched'] for r in job['results']))
                app.export_worker(job,[0,2]);self.assertEqual(job['state'],'done',job['message'])
                result=PdfReader(job['dir']/'cleaned.pdf')
                self.assertEqual(len(result.pages),3)
                # The native page keeps exact processed pixels, not a resampled overlay.
                expected=red_scans.clean(Image.fromarray(a))[0]
                np.testing.assert_array_equal(np.array(result.pages[0].images[0].image),expected)
                np.testing.assert_array_equal(np.array(result.pages[1].images[0].image),a)
                self.assertEqual(result.pages[2].rotation,90)
                np.testing.assert_array_equal(np.array(engine.render_page(job['dir']/'cleaned.pdf',2)),np.array(Image.open(app.image_path(job,2,True))))
                with client.get('/api/download/'+key) as download:
                    self.assertEqual(download.status_code,200)
                self.assertEqual(source.read_bytes(),original)
                with self.assertRaises(InterruptedError):red_scans.prepare(job['source'],0,job['dir'],lambda:True)
            finally:
                job['state']='ready';client.post('/api/clear/'+key,json={},headers=headers)


if __name__=='__main__':unittest.main()
