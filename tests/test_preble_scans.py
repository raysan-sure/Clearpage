"""Synthetic halftone preservation, native export and API regressions."""
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (NameObject, NumberObject, DictionaryObject, DecodedStreamObject)
from clearpage.presets import preble_scans as preble
import app
from clearpage.pdf import engine

ASSETS = RESOURCE_ROOT/'assets'


def example():
    with np.load(ASSETS/'preble-unofficial.npz') as data: mask = data['mask']
    mask = cv2.resize(mask, (2536, round(580*2536/768)), interpolation=cv2.INTER_NEAREST)
    mask = np.pad(mask, ((0, 3304-mask.shape[0]), (0, 0))) > 0
    y, x = np.indices(mask.shape)
    a = np.full(mask.shape, 255, 'uint8')
    a[mask & ((x+y) % 3 == 0)] = 0
    a[1150:1155, 150:2300] = 0  # Rule across the watermark.
    a[1200, 150:2300] = 0  # A genuinely thin rule must survive too.
    a[800:1400, 1100:1105] = 0
    a[1100:1120, 1000:1040] = 0  # Solid document ink.
    a[2500:2550, 600:1000] = 0
    a[2800, 100] = 0  # Small punctuation outside the selected glyphs.
    return a, mask


def fixture(path, a, pages=2, inset=False, page_height=800):
    h, w = a.shape; writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(612, page_height)
        image = DecodedStreamObject()
        image.update({NameObject('/Type'): NameObject('/XObject'),
                      NameObject('/Subtype'): NameObject('/Image'),
                      NameObject('/Width'): NumberObject(w), NameObject('/Height'): NumberObject(h),
                      NameObject('/BitsPerComponent'): NumberObject(1),
                      NameObject('/ColorSpace'): NameObject('/DeviceGray')})
        image.set_data(np.packbits(a >= 128, axis=1).tobytes())
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Scan'): writer._add_object(image.flate_encode())})})
        width=608 if inset else 612; height=width*h/w
        stream=DecodedStreamObject()
        stream.set_data(f'q\n{width} 0 0 {height} {(612-width)/2} {page_height-height} cm\n/Scan Do\nQ'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


class PrebleTests(unittest.TestCase):
    def test_edge_guard_removes_attached_fringe_without_shifting_ink(self):
        ink = np.zeros((160, 240), 'uint8')
        ink[35:75, 70:76] = 1
        ink[74, 65:83] = 1  # Thin horizontal serif on a solid stem.
        ink[50:56, 110:116] = 1  # Punctuation with a solid interior.
        ink[100, 10:230] = 1  # Thin form rule.
        truth = ink.copy()
        ink[75:77, 66:83:3] = 1  # Watermark fringe below the serif.
        ink[34, 70:76:3] = 1
        cleaned = ink & preble.ink_guard(ink)
        self.assertTrue(np.array_equal(cleaned, truth))
        # The former 2x2 opening shifted the guard down and right. Reflection
        # must give identical protection on all sides of document characters.
        reflected = np.flip(ink).copy()
        self.assertTrue(np.array_equal(preble.ink_guard(ink), np.flip(preble.ink_guard(reflected))))

    def test_cleanup_retains_solid_ink_rules_and_outside_pixels(self):
        a, mask = example(); result, details = preble.clean_pixels(a, ASSETS)
        self.assertTrue(details['matched'], details)
        self.assertGreater(details['changed'], 12000)
        self.assertTrue(np.array_equal(a[1150:1155], result[1150:1155]))
        self.assertTrue(np.array_equal(a[1200], result[1200]))
        self.assertTrue(np.array_equal(a[:,1100:1105], result[:,1100:1105]))
        self.assertTrue(np.array_equal(a[1100:1120,1000:1040], result[1100:1120,1000:1040]))
        self.assertTrue(np.array_equal(a[2000:], result[2000:]))
        self.assertTrue(np.all(result[a==255]==255))

    def test_blank_solid_watermark_and_other_layout_are_unchanged(self):
        a, mask = example()
        for pixels in (np.full_like(a,255), np.where(mask,0,255).astype('uint8'), np.roll(a,500,axis=1)):
            result, details = preble.clean_pixels(pixels, ASSETS)
            self.assertFalse(details['matched'])
            self.assertTrue(np.array_equal(pixels,result))

    def test_preview_selection_export_geometry_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.pdf';target=root/'output.pdf'
            a, _ = example(); fixture(source,a,inset=True,page_height=1008)
            before=source.read_bytes(); reader=PdfReader(source)
            self.assertIsNotNone(preble.scan_object(reader.pages[0]))
            reader.pages[0].rotate(90)
            self.assertIsNone(preble.scan_object(reader.pages[0]))
            with self.assertRaises(InterruptedError):
                preble.prepare(source,0,root,ASSETS,lambda:True)
            self.assertFalse(list(root.glob('*.part')))
            options=app.settings(dict(method='preble_unofficial',mode='lama',fix_negative=True,auto_restore=True))
            self.assertFalse(options['auto_restore']);self.assertFalse(options['fix_negative'])
            client=app.app.test_client(); headers={'X-ClearPage':'local'}
            response=client.post('/api/upload',data=before,headers=headers)
            self.assertEqual(response.status_code,200,response.json)
            job=app.JOBS[response.json['id']]
            try:
                response=client.post('/api/preview/'+job['id'],json=dict(method='preble_unofficial',page=0),headers=headers)
                self.assertEqual(response.status_code,200,response.json)
                self.assertTrue(response.json['matched'])
                job.update(options=options,results=[],cancel=False)
                app.batch(job,options)
                self.assertEqual(job['state'],'review',job['message'])
                app.export_worker(job,[0])
                self.assertEqual(job['state'],'done',job['message'])
                result=PdfReader(job['dir']/'cleaned.pdf'); original=PdfReader(source)
                self.assertEqual(len(result.pages),2)
                expected,_=preble.clean_pixels(a,ASSETS)
                for i,wanted in enumerate((expected,a)):
                    p=result.pages[i]
                    self.assertTrue(np.array_equal(np.array(p.images[0].image.convert('L')),wanted))
                    self.assertEqual(p.mediabox,original.pages[i].mediabox)
                    self.assertEqual(p.get_contents().get_data(),original.pages[i].get_contents().get_data())
                self.assertEqual(source.read_bytes(),before)
                self.assertEqual(client.get('/static/type-preble.webp').status_code,200)
            finally:
                job['state']='ready'
                client.post('/api/clear/'+job['id'],json={},headers=headers)


if __name__=='__main__': unittest.main()
