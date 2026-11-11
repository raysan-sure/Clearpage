"""Synthetic A-PDF layouts; supplied documents are never committed."""
import math
import tempfile
import unittest
from pathlib import Path
import numpy as np
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, ContentStream, DecodedStreamObject,
                           DictionaryObject, FloatObject, NameObject)
from clearpage.pdf import engine
from clearpage.pdf import pdf_overlays as overlay


def sample(path, tiled=False, mutation=None, nested=False):
    writer=PdfWriter()
    font=DictionaryObject({NameObject(k):NameObject(v) for k,v in {
        '/Type':'/Font','/Subtype':'/Type1','/BaseFont':'/Helvetica','/Encoding':'/WinAnsiEncoding'}.items()})
    state=DictionaryObject({NameObject('/Type'):NameObject('/ExtGState'),NameObject('/SMask'):NameObject('/None'),
        NameObject('/ca'):FloatObject(.44 if tiled else .51),NameObject('/CA'):FloatObject(.44 if tiled else .51)})
    resources=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font),
        NameObject('/QQAPFtest'):writer._add_object(font)}),
        NameObject('/ExtGState'):DictionaryObject({NameObject('/QQAPGStest'):writer._add_object(state)})})
    # Normal body text deliberately contains both watermark phrases.
    body=b'q BT /F1 12 Tf 1 0 0 1 25 100 Tm (Body: Sample and www.apdf.com 12345) Tj ET Q'
    text='www.apdf.com' if tiled else 'Sample'
    c=math.cos(math.radians(21 if tiled else 45));s=math.sin(math.radians(21 if tiled else 45))
    code=['q /QQAPGStest gs']
    for i in range(3 if tiled else 1):
        x,y=20+140*i,200
        code.append(f'{c:.8f} {s:.8f} {-s:.8f} {c:.8f} {x} {y} cm')
        if i==0:
            code.append('0 Tr /QQAPFtest '+('30 Tf .8196 .4941 .6235' if tiled else '103.1638 Tf 0 0 1')+' rg 100 Tz 0 Ts 0 Tw 0 Tc')
        code.append(f'BT 1 0 0 1 0 0 Tm ({text}) Tj ET {c:.8f} {-s:.8f} {s:.8f} {c:.8f} 0 0 cm 1 0 0 1 {-x} {-y} cm')
    code.append('Q');mark='\n'.join(code).encode()
    if mutation:mark=mutation(mark,font,state)
    def stream(data):
        obj=DecodedStreamObject();obj.set_data(data);return writer._add_object(obj)
    contents=ArrayObject([stream(mark),stream(body)])
    if nested:
        form=DecodedStreamObject();form.set_data(mark+b'\n'+body)
        form.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),
                     NameObject('/BBox'):ArrayObject([FloatObject(x) for x in [0,0,612,792]]),NameObject('/Resources'):resources})
        resources=DictionaryObject({NameObject('/XObject'):DictionaryObject({NameObject('/Shared'):writer._add_object(form)})})
        contents=stream(b'q /Shared Do Q')
    for _ in range(2):
        page=writer.add_blank_page(612,792);page[NameObject('/Resources')]=resources;page[NameObject('/Contents')]=contents
    writer.write(path)
    return body


class APDFTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.pdf';self.target=self.root/'clean.pdf'
    def tearDown(self):self.tmp.cleanup()
    def test_both_styles_preserve_body_and_unselected_page(self):
        for tiled in [False,True]:
            with self.subTest(tiled=tiled):
                body=sample(self.source,tiled)
                self.assertEqual([p['overlays'] for p in overlay.inspect(self.source)],[1,1])
                overlay.write_clean(self.source,self.target,[0])
                self.assertEqual([p['overlays'] for p in overlay.inspect(self.target)],[0,1])
                original=overlay.read_pdf(self.source);clean=overlay.read_pdf(self.target)
                self.assertEqual(clean.pages[0].extract_text(),'Body: Sample and www.apdf.com 12345')
                self.assertEqual(ContentStream(clean.pages[0].get_contents(),clean).operations,
                                 ContentStream(original.pages[0]['/Contents'][1],original).operations)
                self.assertTrue(np.array_equal(engine.render_page(self.source,1),engine.render_page(self.target,1)))
                self.assertEqual(original.pages[0].mediabox,clean.pages[0].mediabox)
    def test_shared_form_is_copy_on_write(self):
        sample(self.source,tiled=True,nested=True)
        overlay.write_clean(self.source,self.target,[0])
        self.assertEqual([p['overlays'] for p in overlay.inspect(self.target)],[0,1])
        self.assertTrue(np.array_equal(engine.render_page(self.source,1),engine.render_page(self.target,1)))
    def test_ambiguous_blocks_are_kept(self):
        def custom_font(mark,font,state):
            font[NameObject('/Encoding')]=NameObject('/MacRomanEncoding');return mark
        def opaque(mark,font,state):
            state[NameObject('/ca')]=FloatObject(1);return mark
        mutations=[lambda m,f,s:m.replace(b'(Sample)',b'(Other words)'),
                   lambda m,f,s:m.replace(b'(Sample) Tj',b'(Sample) Tj (Important body text) Tj'),
                   lambda m,f,s:m.replace(b'0 Tr',b'7 Tr'),
                   lambda m,f,s:m[:-1],custom_font,opaque]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                sample(self.source,mutation=mutation)
                self.assertEqual([p['overlays'] for p in overlay.inspect(self.source)],[0,0])
                overlay.write_clean(self.source,self.target,[0])
                self.assertTrue(np.array_equal(engine.render_page(self.source,0),engine.render_page(self.target,0)))


if __name__=='__main__':unittest.main()
