"""Synthetic regression coverage for negative scans and reference comparisons."""
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter
import json
import benchmark
import app
from clearpage.pdf import engine
from clearpage.pdf import page_polarity
from clearpage.pdf import quality_check


def text_page(negative=False):
    page = Image.new('RGB', (600, 800), 'black' if negative else 'white')
    font = ImageFont.truetype('C:/Windows/Fonts/times.ttf', 20)
    draw = ImageDraw.Draw(page)
    for row in range(16):
        draw.text((35, 60+row*35), 'Original text 12345 - no words replaced',
                  fill='white' if negative else 'black', font=font)
    return page


def pdf(images):
    output=BytesIO(); c=Canvas(output, pagesize=(300, 400))
    for image in images:
        c.drawImage(ImageReader(image),0,0,300,400); c.showPage()
    c.save(); return output.getvalue()


class QualityTests(unittest.TestCase):
    def test_negative_scan_exact_inversion_and_normal_unchanged(self):
        negative=text_page(True); normal=text_page()
        result, info=page_polarity.correct(negative)
        self.assertTrue(info['detected'])
        self.assertTrue(np.array_equal(result, np.array(normal)))
        result, info=page_polarity.correct(normal)
        self.assertFalse(info['applied'])
        self.assertTrue(np.array_equal(result,np.array(normal)))

    def test_blank_photo_gradient_color_and_border_not_automatically_inverted(self):
        photo=Image.fromarray(np.tile(np.linspace(0,255,600,dtype='uint8'),(800,1))).convert('RGB')
        border=Image.new('RGB',(600,800),'black');border.paste(text_page().resize((480,640)),(60,80))
        tinted=text_page(True); a=np.array(tinted); a[a[:,:,0]==0]=[0,0,80]
        for image in (Image.new('RGB',(600,800),'black'), photo, border, Image.fromarray(a)):
            result, info=page_polarity.correct(image)
            self.assertFalse(info['applied'])
            self.assertTrue(np.array_equal(result,np.array(image)))
            forced, info=page_polarity.correct(image,True)
            self.assertTrue(info['forced'])
            self.assertTrue(np.array_equal(forced,255-np.array(image)))

    def test_metrics_distinguish_restoration_from_text_damage(self):
        reference=text_page(); source=reference.copy()
        ImageDraw.Draw(source).rectangle((0,0,599,20),fill='black')
        perfect=quality_check.compare(source,reference,reference)
        self.assertEqual(perfect['correction_error_after'],0)
        self.assertEqual(perfect['dark_pixel_recall_percent'],100)
        self.assertEqual(perfect['preserved_reference_matching_pixels_percent'],100)
        blank=quality_check.compare(source,Image.new('RGB',reference.size,'white'),reference)
        self.assertEqual(blank['dark_pixel_recall_percent'],0)
        self.assertIsNone(blank['dark_pixel_precision_percent'])
        self.assertLess(blank['preserved_reference_matching_pixels_percent'],100)
        same=quality_check.compare(reference,reference,reference)
        self.assertIsNone(same['correction_error_after'])
        with self.assertRaises(ValueError): quality_check.compare(source,reference,reference.resize((200,200)))

    def test_api_batch_export_reference_and_mode_guards(self):
        client=app.app.test_client()
        with client.get('/'): pass
        headers={'X-ClearPage':'local'}
        upload=client.post('/api/upload',data=pdf([text_page(True),text_page()]),headers=headers)
        self.assertEqual(upload.status_code,200)
        identifier=upload.json['id']; job=app.JOBS[identifier]
        post=lambda route,body:client.post(route,json=body,headers=headers)
        try:
            options=app.settings(dict(method='polarity',auto_restore=True,use_reviewed=True,scan_inversion=True))
            self.assertFalse(options['auto_restore']);self.assertFalse(options['use_reviewed']);self.assertFalse(options['scan_inversion'])
            # Repairs from another mode must not leak into an inverted page.
            job['repairs']={0:[dict(box=(0,0,20,20),patch=Image.new('RGB',(20,20),'red'))]}
            job['options']=options;job['results']=[];job['state']='processing'
            app.batch(job,options)
            self.assertEqual(job['state'],'review')
            self.assertTrue(job['results'][0]['uncertain'])
            self.assertEqual(job['results'][1]['changed'],0)
            expected=255-np.array(Image.open(app.original(job,0)).convert('RGB'))
            self.assertTrue(np.array_equal(expected,np.array(Image.open(app.image_path(job,0,True)))))
            self.assertEqual(post('/api/repair/'+identifier+'/preview',dict(page=0)).status_code,400)
            # Unselected inversion must retain the original in the complete PDF.
            app.export_worker(job,[])
            self.assertTrue(np.array_equal(engine.render_page(job['source'],0),engine.render_page(job['dir']/'cleaned.pdf',0)))
            app.export_worker(job,[0])
            self.assertEqual(len(PdfReader(job['dir']/'cleaned.pdf').pages),2)
            self.assertTrue(np.array_equal(expected,np.array(engine.render_page(job['dir']/'cleaned.pdf',0))))
            self.assertTrue(np.array_equal(engine.render_page(job['source'],1),engine.render_page(job['dir']/'cleaned.pdf',1)))
            reference=pdf([text_page(),text_page()])
            report=client.post('/api/compare/'+identifier+'/0',data=reference,headers=headers)
            self.assertEqual(report.status_code,200,report.json)
            self.assertLess(report.json['mean_rgb_error'],1)
            self.assertFalse((job['dir']/'quality-reference.pdf').exists())
            invalid=client.post('/api/compare/'+identifier+'/0',data=pdf([text_page()]),headers=headers)
            self.assertEqual(invalid.status_code,400)
            self.assertFalse((job['dir']/'quality-reference.pdf').exists())
            app.batch(job,options | dict(polarity_pages=[1]))
            self.assertTrue(job['results'][-1]['polarity']['forced'])
        finally:
            job['state']='ready';post('/api/clear/'+identifier,{})

    def test_rotation_crop_and_repeat_do_not_double_invert(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);source=p/'source.pdf';source.write_bytes(pdf([text_page(True)]))
            writer=PdfWriter();writer.clone_document_from_reader(PdfReader(source))
            writer.pages[0].rotate(90);writer.pages[0].cropbox.lower_left=(10,20)
            writer.write(source)
            image=engine.render_page(source,0);image.save(p/'before.png')
            corrected,info=page_polarity.correct(image,True)
            Image.fromarray(corrected).save(p/'after.png')
            engine.write_pdf(source,p/'result.pdf',{0:(p/'before.png',p/'after.png')})
            self.assertTrue(np.array_equal(corrected,engine.render_page(p/'result.pdf',0)))
            again,_=page_polarity.correct(image,True)
            self.assertTrue(np.array_equal(corrected,again))
            a=PdfReader(source).pages[0];b=PdfReader(p/'result.pdf').pages[0]
            self.assertEqual(a.cropbox,b.cropbox);self.assertEqual(a.rotation,b.rotation)

    def test_benchmark_categories_and_geometry_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'source.pdf').write_bytes(pdf([text_page(True),text_page()]))
            (p/'reference.pdf').write_bytes(pdf([text_page(),text_page()]))
            (p/'result.pdf').write_bytes((p/'reference.pdf').read_bytes())
            case=dict(id='synthetic',category='negative',source='source.pdf',reference='reference.pdf',result='result.pdf')
            (p/'cases.json').write_text(json.dumps(dict(cases=[case])))
            report=benchmark.run(p/'cases.json')
            self.assertEqual(len(report['pages']),2)
            self.assertEqual(report['categories']['negative']['metrics']['mean_rgb_error']['mean_per_page'],0)
            writer=PdfWriter();writer.clone_document_from_reader(PdfReader(p/'reference.pdf'))
            writer.pages[0].rotate(180);writer.write(p/'reference.pdf')
            with self.assertRaises(ValueError):benchmark.run(p/'cases.json')


if __name__=='__main__':unittest.main(verbosity=2)
