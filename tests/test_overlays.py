"""Synthetic regression tests; company documents are not redistributed."""
from pathlib import Path
from io import BytesIO
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageDraw
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfWriter
from pypdf.generic import NameObject, DecodedStreamObject, DictionaryObject
from clearpage.pdf import pdf_overlays as overlay
from clearpage.pdf import engine
import app


def sample(path, pair=True):
    im=Image.new('RGB',(300,400),'white');d=ImageDraw.Draw(im)
    d.text((10,40),'Original wording 12345',fill='black');d.line((10,90,280,90),fill='black',width=3)
    c=Canvas(str(path),pagesize=(300,400))
    c.beginForm('shared',0,0,300,400)
    c.drawImage(ImageReader(im),0,0,300,400)
    for y,text in [(300,'UNOFFICIAL COPY')]+([(200,"Property of Cook County Clerk's Office")] if pair else []):
        c._code.append(f'q 1 0 0 1 5 {y} cm BT /F1 12 Tf .8 .8 .8 rg ({text}) Tj ET Q')
    c.endForm()
    for _ in range(2):c.doForm('shared');c.showPage()
    c.save()


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.pdf';sample(self.source)
    def tearDown(self): self.tmp.cleanup()
    def test_shared_form_unchecked_page_and_geometry(self):
        w=PdfWriter();w.clone_document_from_reader(overlay.read_pdf(self.source))
        w.pages[0].rotate(90);w.pages[0].cropbox.lower_left=(5,6)
        w.write(self.source)
        target=self.root/'out.pdf';overlay.write_clean(self.source,target,[0])
        self.assertEqual([p['overlays'] for p in overlay.inspect(target)],[0,2])
        self.assertTrue(np.array_equal(engine.render_page(self.source,1),engine.render_page(target,1)))
        a=overlay.read_pdf(self.source).pages[0];b=overlay.read_pdf(target).pages[0]
        self.assertEqual(a.cropbox,b.cropbox);self.assertEqual(a.rotation,b.rotation)
        self.assertEqual(a.images[0].data,b.images[0].data)
    def test_body_phrase_alone_and_unrecognized_vector_preserved(self):
        sample(self.source,pair=False)
        self.assertFalse(any(p['overlays'] for p in overlay.inspect(self.source)))
        target=self.root/'out.pdf';overlay.write_clean(self.source,target,[0,1])
        self.assertTrue(np.array_equal(engine.render_page(self.source,0),engine.render_page(target,0)))
    def test_vector_fingerprint_rejects_modified_resource(self):
        w=PdfWriter();p=w.add_blank_page(300,400)
        form=DecodedStreamObject();form.set_data(b'0 0 10 10 re f')
        form[NameObject('/Subtype')]=NameObject('/Form')
        signature=overlay.form_signature(form)
        p[NameObject('/Resources')]=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Mark'):w._add_object(form)})})
        stream=DecodedStreamObject();stream.set_data(b'q /Mark Do Q');p[NameObject('/Contents')]=w._add_object(stream)
        w.write(self.source)
        with patch.object(overlay,'OKCOUNTY_SIGNATURES',{signature}):
            self.assertEqual(overlay.inspect(self.source)[0]['overlays'],1)
            form.set_data(b'0 0 10 10 re f 20 20 10 10 re f');w.write(self.source)
            self.assertEqual(overlay.inspect(self.source)[0]['overlays'],0)
    def test_empty_password_and_real_password(self):
        w=PdfWriter();w.clone_document_from_reader(overlay.read_pdf(self.source));w._ID=None;w.encrypt('',owner_password='owner')
        w.write(self.source);self.assertEqual(engine.inspect_pdf(self.source)[0],2)
        w.encrypt('secret');w.write(self.source)
        with self.assertRaises(ValueError):engine.inspect_pdf(self.source)
    def test_cancellation_does_not_replace_output(self):
        target=self.root/'out.pdf';target.write_bytes(b'previous output')
        with self.assertRaises(InterruptedError):overlay.write_clean(self.source,target,[0],lambda:True)
        self.assertEqual(target.read_bytes(),b'previous output')
    def test_application_overlay_preview_export_and_repair_guard(self):
        client=app.app.test_client();client.get('/')
        headers={'X-ClearPage':'local'}
        response=client.post('/api/upload',data=self.source.read_bytes(),headers=headers)
        self.assertEqual(response.status_code,200,response.json);job=response.json;identifier=job['id']
        def post(route,data):return client.post(route,json=data,headers=headers)
        options={'method':'overlay','mode':'lama','auto_restore':True,'scan_inversion':True}
        try:
            preview=post('/api/preview/'+identifier,dict(options,page=0));self.assertTrue(preview.json['structural'],preview.json)
            self.assertFalse(app.JOBS[identifier]['options']['auto_restore'])
            app.JOBS[identifier].update(state='processing',results=[])
            app.batch(app.JOBS[identifier],app.settings(options))
            self.assertEqual(app.JOBS[identifier]['state'],'review')
            self.assertEqual(post('/api/restore/'+identifier,{}).status_code,400)
            self.assertEqual(post('/api/repair/'+identifier+'/preview',{'page':0}).status_code,400)
            app.export_worker(app.JOBS[identifier],[0])
            self.assertEqual(app.JOBS[identifier]['state'],'done')
            target=app.JOBS[identifier]['dir']/'cleaned.pdf'
            self.assertEqual([p['overlays'] for p in overlay.inspect(target)],[0,2])
            self.assertTrue(np.array_equal(engine.render_page(target,0),np.array(Image.open(app.image_path(app.JOBS[identifier],0,True)))))
            self.assertEqual(client.get('/api/download/'+identifier).status_code,200)
        finally:post('/api/clear/'+identifier,{})


if __name__=='__main__': unittest.main(verbosity=2)
