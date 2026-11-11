"""Synthetic native-dot, preservation, export and application regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (NameObject, NumberObject, ArrayObject, ByteStringObject,
                          DictionaryObject, DecodedStreamObject)
import app
from clearpage.pdf import engine
from clearpage.presets import dotted_scans as dots


def fixture(path, pixels, pages=1, size=None):
    h, w = pixels.shape
    writer = PdfWriter()
    for _ in range(pages):
        pw, ph = size or (w/2, h/2)
        page = writer.add_blank_page(pw, ph)
        image = DecodedStreamObject()
        image.update({NameObject('/Type'): NameObject('/XObject'),
                      NameObject('/Subtype'): NameObject('/Image'),
                      NameObject('/Width'): NumberObject(w), NameObject('/Height'): NumberObject(h),
                      NameObject('/BitsPerComponent'): NumberObject(1),
                      NameObject('/ColorSpace'): ArrayObject([NameObject('/Indexed'), NameObject('/DeviceRGB'), NumberObject(1), ByteStringObject(b'\xff\xff\xff\x00\x00\x00')])})
        image[NameObject('/ColorSpace')] = writer._add_object(image['/ColorSpace'])
        image.set_data(np.packbits(pixels < 128, axis=1).tobytes())
        image = image.flate_encode()
        image[NameObject('/Filter')] = ArrayObject([NameObject('/FlateDecode')])
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Scan'): writer._add_object(image)})})
        stream = DecodedStreamObject()
        stream.set_data(f'q\n{pw} 0 0 {ph} 0 0 cm\n/Scan Do\nQ'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as output:
        writer.write(output)


def example(method='dotted_document'):
    if method == 'dotted_document':
        a = np.full((1400, 1001), 255, 'uint8')  # Non-byte-aligned width.
        y, x = 650, 460
    else:
        a = np.full((1800, 2584), 255, 'uint8')
        y, x = 900, 1250
    a[y:y+80:2, x:x+80:2] = 0
    a[y+30:y+34, x-40:x+130] = 0  # A connected map/text stroke across the mark.
    a[y-40:y+130, x+30:x+34] = 0
    a[40:44, 40:90] = 0
    a[100, 100] = 0  # Isolated punctuation outside the watermark band.
    return a, (y, x)


class DottedScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root/'input.pdf'
        self.target = self.root/'output.pdf'
        self.client = app.app.test_client()
        with self.client.get('/'): pass
        self.headers = {'X-ClearPage': 'local'}
        self.ids = []

    def tearDown(self):
        for key in self.ids:
            app.JOBS[key]['state'] = 'ready'
            self.client.post('/api/clear/'+key, json={}, headers=self.headers)
        self.tmp.cleanup()

    def upload(self):
        response = self.client.post('/api/upload', data=self.source.read_bytes(), headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.ids.append(response.json['id'])
        return app.JOBS[self.ids[-1]]

    def test_both_layouts_preserve_connected_strokes_and_outside_pixels(self):
        for method in dots.METHODS:
            a, (y, x) = example(method)
            result, count = dots.clean_pixels(a, method)
            self.assertGreater(count, 500)
            changed = a != result
            allowed = np.zeros(a.shape, bool)
            allowed[y:y+80:2, x:x+80:2] = True
            self.assertFalse(np.any(changed & ~allowed))
            self.assertTrue(np.array_equal(result[y+30:y+34], a[y+30:y+34]))
            self.assertTrue(np.array_equal(result[:, x+30:x+34], a[:, x+30:x+34]))
            self.assertTrue(np.all(result[a == 255] == 255))

    def test_clean_pages_and_sparse_dust_are_unchanged(self):
        a = np.full((1400, 1001), 255, 'uint8')
        a[::50, ::50] = 0
        a[600:605, 200:800] = 0
        for method in dots.METHODS:
            result, count = dots.clean_pixels(a, method)
            self.assertEqual(count, 0)
            self.assertTrue(np.array_equal(a, result))

    def test_full_app_preview_export_selection_cache_and_polarity(self):
        a, _ = example()
        fixture(self.source, a, pages=2)
        source_bytes = self.source.read_bytes()
        job = self.upload()
        response = self.client.post('/api/preview/'+job['id'], json={'method': 'dotted_document', 'page': 0}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['matched'])
        options = app.settings(dict(method='dotted_document', mode='lama', auto_restore=True, fix_negative=True))
        self.assertFalse(options['auto_restore']); self.assertFalse(options['fix_negative'])
        job.update(options=options, results=[], cancel=False)
        app.batch(job, options)
        self.assertEqual(job['state'], 'review', job['message'])
        self.assertTrue(all(r['uncertain'] for r in job['results']))
        app.export_worker(job, [0])
        self.assertEqual(job['state'], 'done', job['message'])
        output = job['dir']/'cleaned.pdf'
        expected, _ = dots.clean_pixels(a, 'dotted_document')
        for i, pixels in enumerate((expected, a)):
            page = PdfReader(output).pages[i]
            name, obj = dots.scan_object(page)
            self.assertEqual(obj.get_data(), np.packbits(pixels < 128, axis=1).tobytes())
            original = PdfReader(self.source).pages[i]
            self.assertEqual(page.mediabox, original.mediabox)
            self.assertEqual(page.get_contents().get_data(), original.get_contents().get_data())
        self.assertTrue(np.array_equal(engine.render_page(output, 0), Image.open(app.image_path(job, 0, True))))
        self.assertEqual(self.source.read_bytes(), source_bytes)
        # Backend rejects word restoration for native scan methods too.
        with self.assertRaises(ValueError):
            app.repair_job(job['id'], {})

    def test_large_map_preview_bounded_and_export_native(self):
        a, _ = example('dotted_map')
        fixture(self.source, a, size=(4651, 3240))
        self.assertEqual(engine.inspect_pdf(self.source)[0], 1)
        preview = engine.render_page(self.source, 0)
        self.assertLessEqual(preview.width*preview.height, engine.MAX_PIXELS)
        path, stats = dots.prepare(self.source, 0, 'dotted_map', self.root, preview)
        self.assertTrue(stats['matched'])
        dots.write_clean(self.source, self.target, [0], 'dotted_map', self.root)
        obj = dots.scan_object(PdfReader(self.target).pages[0])[1]
        self.assertEqual((obj['/Width'], obj['/Height']), (a.shape[1], a.shape[0]))

    def test_unsupported_layout_limits_and_cancellation(self):
        a, _ = example(); fixture(self.source, a)
        page = PdfReader(self.source).pages[0]
        page.rotate(90)
        self.assertIsNone(dots.scan_object(page))
        with patch.object(dots, 'MAX_REGION_PIXELS', 100):
            with self.assertRaises(ValueError): dots.clean_pixels(a, 'dotted_document')
        with self.assertRaises(InterruptedError):
            dots.prepare(self.source, 0, 'dotted_document', self.root, engine.render_page(self.source, 0), lambda: True)
        self.assertFalse((self.root/'0-dotted_document.pdf').exists())
        self.assertFalse(list(self.root.glob('*.part')))


if __name__ == '__main__':
    unittest.main()
