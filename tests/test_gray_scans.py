"""Native gray scan preservation, acceptance boundaries and export regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import NumberObject, NameObject
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from clearpage.presets import gray_scans
from clearpage.pdf import engine
from tests.test_scan_patterns import gray_fixture, ASSETS


class NativeGrayTests(unittest.TestCase):
    def make_pdf(self, target, image, text=False):
        canvas=Canvas(str(target),pagesize=(369.84,608.64))
        for _ in range(2):
            canvas.drawImage(ImageReader(image),0,0,369.84,608.64)
            if text: canvas.drawString(10,10,'Retain this independent text object')
            canvas.showPage()
        canvas.save()

    def test_native_pixels_fractional_page_export_and_unselected_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.pdf'
            a=gray_fixture(2536);self.make_pdf(source,Image.fromarray(a))
            digest=source.read_bytes()
            # An older saved workspace may contain an unsupported-layout result.
            for version in ('v1', 'v2', 'v3'):
                (root/f'0-native-gray-{version}.pdf').write_bytes(b'old cached result')
                (root/f'0-native-gray-{version}.json').write_text('{"matched":false}')
            preview,details=gray_scans.prepare(source,0,root,ASSETS)
            self.assertEqual(preview.name,f'0-{gray_scans.CACHE}.pdf')
            self.assertTrue(details['native_gray']);self.assertTrue(details['matched'])
            gray_scans.write_clean(source,root/'output.pdf',[0],root)
            b=np.array(PdfReader(root/'output.pdf').pages[0].images[0].image.convert('RGB'))
            self.assertEqual(a.shape,b.shape)
            np.testing.assert_array_equal(a[a.min(2)<=32],b[a.min(2)<=32])
            with np.load(ASSETS/'unofficial-copy-2536.npz') as data:
                np.testing.assert_array_equal(a[~data['scope']],b[~data['scope']])
            np.testing.assert_array_equal(engine.render_page(preview,0),engine.render_page(root/'output.pdf',0))
            np.testing.assert_array_equal(engine.render_page(source,1),engine.render_page(root/'output.pdf',1))
            self.assertEqual(source.read_bytes(),digest)
            p=PdfReader(source).pages[0];q=PdfReader(root/'output.pdf').pages[0]
            self.assertEqual(p.mediabox,q.mediabox)
            with patch('clearpage.presets.gray_scans.scan_patterns.unofficial_copy',side_effect=AssertionError('cache missed')):
                self.assertEqual(gray_scans.prepare(source,0,root,ASSETS)[0],preview)

    def test_nontrivial_pages_retained_and_cancel_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.pdf'
            self.make_pdf(source,Image.fromarray(gray_fixture()),text=True)
            self.assertIsNone(gray_scans.prepare(source,0,root,ASSETS))
            self.make_pdf(source,Image.fromarray(gray_fixture()))
            p=PdfReader(source).pages[0]
            p[NameObject('/Rotate')]=NumberObject(90)
            self.assertIsNone(gray_scans.scan_object(p))
            with self.assertRaises(InterruptedError):gray_scans.prepare(source,0,root,ASSETS,lambda:True)
            self.assertFalse((root/f'0-{gray_scans.CACHE}.pdf').exists())


if __name__=='__main__':unittest.main()
