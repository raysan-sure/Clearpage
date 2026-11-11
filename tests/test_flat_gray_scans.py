"""Black-pixel preservation and upload/export regressions for separated gray scans."""
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
import zlib
from unittest.mock import patch
import numpy as np
from PIL import Image
from pypdf import PdfReader,PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, NumberObject
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
import app
from clearpage.pdf import engine
from clearpage.presets import flat_gray_scans as flat
from clearpage.presets import tammany_scans


def fixture():
    a=np.full((800,600,3),255,'uint8')
    for y in (20,220,420,620):
        for x in range(20,550,35):a[y:y+24,x:x+20]=211
        a[y+24:y+25,20:550]=240
    for y in range(100,760,40):a[y:y+3,80:540]=0
    a[150:650,150:152]=0
    return a


class FlatGrayTests(unittest.TestCase):
    def test_st_tammany_grayscale_with_indirect_color_space(self):
        outline = Image.fromarray(tammany_scans.probes()[0].astype('uint8')*255)
        mask = np.asarray(outline.resize((1100, 1800), Image.Resampling.NEAREST)) == 255
        pixels = np.where(mask, 160, 255).astype('uint8')
        pixels[400:1400:35, 180:950] = 0
        original = BytesIO()
        canvas = Canvas(original, pagesize=(440, 720))
        canvas.drawImage(ImageReader(Image.fromarray(pixels)), 0, 0, 440, 720)
        canvas.save()
        writer = PdfWriter(clone_from=PdfReader(original))
        scan = next(iter(writer.pages[0]['/Resources']['/XObject'].values())).get_object()
        scan[NameObject('/Filter')] = NameObject('/FlateDecode')
        scan._data = zlib.compress(pixels.tobytes())
        profile = DecodedStreamObject()
        profile[NameObject('/N')] = NumberObject(1)
        space = ArrayObject([NameObject('/ICCBased'), writer._add_object(profile)])
        scan[NameObject('/ColorSpace')] = writer._add_object(space)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'tammany.pdf'
            writer.write(source)
            preview, details = flat.prepare(source, 0, directory)
            self.assertTrue(details['native_tammany'])
            page = PdfReader(preview).pages[0]
            actual = tammany_scans.pixels(tammany_scans.scan_object(page))
            np.testing.assert_array_equal(actual, np.where(pixels == 160, 255, pixels))

    def test_removes_separate_gray_and_preserves_every_black_pixel(self):
        a=fixture();result,mask,stats=flat.clean(Image.fromarray(a))
        self.assertTrue(stats['matched']);self.assertTrue(stats['black_pixels_exact'])
        np.testing.assert_array_equal(result==0,a==0)
        np.testing.assert_array_equal(result[~mask],a[~mask])
        self.assertTrue((result[mask]==255).all())
        self.assertTrue(np.isin(result,[0,255]).all())

    def test_refuses_faint_text_color_and_non_repeated_gray_content(self):
        mid=fixture();mid[100,100]=160
        color=fixture();color[100,100]=(0,0,100)
        clean=np.full((800,600,3),255,'uint8');clean[100:103,100:500]=0
        stamp=clean.copy();stamp[650:700,50:180]=211
        for a in (mid,color,clean,stamp):
            result,mask,stats=flat.clean(Image.fromarray(a))
            np.testing.assert_array_equal(result,a)
            self.assertFalse(mask.any());self.assertFalse(stats['matched'])

    def test_cancel_and_pixel_limit(self):
        with self.assertRaises(InterruptedError):flat.clean(Image.new('RGB',(10,10)),lambda:True)
        with patch.object(flat,'MAX_PIXELS',1):
            with self.assertRaises(ValueError):flat.clean(Image.new('RGB',(10,10)))

    def test_application_offset_rotation_selective_export_and_unsupported_page(self):
        client=app.app.test_client()
        with client.get('/'):pass
        headers={'X-ClearPage':'local'}
        with tempfile.TemporaryDirectory() as directory:
            a=fixture();buffer=BytesIO();c=Canvas(buffer,pagesize=(400,600))
            for _ in range(2):
                c.drawImage(ImageReader(Image.fromarray(a)),25,100,300,400);c.showPage()
            c.drawString(25,100,'Digital text must remain unchanged');c.showPage();c.save()
            reader=PdfReader(buffer);reader.pages[0].rotate(90)
            writer=PdfWriter()
            for page in reader.pages:writer.add_page(page)
            source=Path(directory)/'input.pdf';writer.write(source);data=source.read_bytes()
            response=client.post('/api/upload',data=data,headers=headers)
            self.assertEqual(response.status_code,200,response.json)
            key=response.json['id'];job=app.JOBS[key]
            try:
                response=client.post('/api/preview/'+key,json={'method':'flat_gray_copy','page':0},headers=headers)
                self.assertEqual(response.status_code,200,response.json)
                self.assertTrue(response.json['black_pixels_exact'])
                options=app.settings({'method':'flat_gray_copy','mode':'lama','auto_restore':True})
                self.assertFalse(options['auto_restore']);self.assertEqual(options['mode'],'careful')
                job.update(options=options,results=[],cancel=False);app.batch(job,options)
                self.assertEqual(job['state'],'review',job['message'])
                self.assertFalse(job['results'][2]['matched'])
                app.export_worker(job,[0,2]);self.assertEqual(job['state'],'done',job['message'])
                output=PdfReader(job['dir']/'cleaned.pdf');original=PdfReader(source)
                self.assertEqual(len(output.pages),3)
                for p,q in zip(original.pages,output.pages):
                    self.assertEqual(list(p.mediabox),list(q.mediabox));self.assertEqual(p.rotation,q.rotation)
                    self.assertEqual(p.get_contents().get_data(),q.get_contents().get_data())
                np.testing.assert_array_equal(np.array(output.pages[0].images[0].image),flat.clean(Image.fromarray(a))[0])
                np.testing.assert_array_equal(np.array(output.pages[1].images[0].image),a)
                np.testing.assert_array_equal(np.array(engine.render_page(job['dir']/'cleaned.pdf',0)),np.array(Image.open(app.image_path(job,0,True))))
                with client.get('/api/download/'+key) as download:self.assertEqual(download.status_code,200)
                self.assertEqual(source.read_bytes(),data)
                with self.assertRaises(InterruptedError):flat.prepare(job['source'],0,job['dir'],lambda:True)
            finally:
                job['state']='ready';client.post('/api/clear/'+key,json={},headers=headers)


if __name__=='__main__':unittest.main()
