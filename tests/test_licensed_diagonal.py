"""Synthetic tests; fixtures contain no customer document content."""
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
from clearpage.presets import licensed_diagonal as licensed
import app


def example(height=3312):
    a = np.full((height, licensed.WIDTH), 255, 'uint8')
    for y in range(400, 2600, 70):
        cv2.putText(a, 'Original words 12345', (300, y), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    a = np.where(a < 128, 0, 255).astype('uint8')
    marked = a.copy(); marked[:3312][licensed.profile()[0]] = 0
    return a, marked


def fixture(path, scans):
    w = PdfWriter()
    for scan in scans:
        h = scan.shape[0]; p = w.add_blank_page(612, 792)
        objects = DictionaryObject(); offset = 0
        for i, band_height in enumerate(licensed.HEIGHTS[h]):
            obj = DecodedStreamObject()
            obj.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                        NameObject('/ColorSpace'): NameObject('/DeviceGray'), NameObject('/BitsPerComponent'): NumberObject(8),
                        NameObject('/Width'): NumberObject(licensed.WIDTH), NameObject('/Height'): NumberObject(band_height)})
            obj.set_data(scan[offset:offset+band_height].tobytes()); offset += band_height
            objects[NameObject(f'/Image{i+1}')] = w._add_object(obj.flate_encode())
        p[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): objects})
        stream = DecodedStreamObject(); stream.set_data(licensed.profile()[2][h])
        p[NameObject('/Contents')] = w._add_object(stream)
    w.write(path)


class LicensedDiagonalTests(unittest.TestCase):
    def test_blank_stamp_removed_both_heights(self):
        for h in licensed.HEIGHTS:
            a = np.full((h, licensed.WIDTH), 255, 'uint8')
            a[:3312][licensed.profile()[0]] = 0
            before = a.copy(); result, info = licensed.clean_pixels(a)
            self.assertTrue(info['matched']); self.assertTrue((result == 255).all())
            self.assertTrue(np.array_equal(a, before))

    def test_ink_outside_stamp_is_identical(self):
        _, a = example()
        result, info = licensed.clean_pixels(a)
        self.assertTrue(info['uncertain'])
        self.assertTrue(np.array_equal(result[~licensed.profile()[0]], a[~licensed.profile()[0]]))
        self.assertEqual(set(np.unique(result)), {0, 255})

    def test_nonmatches_and_cancellation(self):
        plain, marked = example()
        for scan in (plain, np.roll(marked, 1, axis=0), np.roll(marked, 1, axis=1),
                     marked[::2, ::2], np.zeros_like(marked), np.full_like(marked, 255),
                     np.where(marked == 0, 30, 255).astype('uint8')):
            result, info = licensed.clean_pixels(scan)
            self.assertFalse(info['matched']); self.assertTrue(np.array_equal(result, scan))
        with self.assertRaises(InterruptedError): licensed.clean_pixels(marked, lambda: True)

    def test_application_suggestions_preview_export_and_unselected_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'test.pdf'
            plain, marked = example(); _, tall = example(4208)
            fixture(source, [marked, tall, plain]); before = source.read_bytes()
            client = app.app.test_client(); client.get('/').close()
            headers = {'X-ClearPage': 'local'}
            uploaded = client.post('/api/upload', data=before, headers=headers)
            self.assertEqual(uploaded.status_code, 200, uploaded.json)
            key = uploaded.json['id']; job = app.JOBS[key]
            try:
                suggestions = client.get('/api/suggestions/'+key).json
                self.assertEqual(suggestions['items'][0]['method'], 'licensed_diagonal')
                self.assertEqual(suggestions['items'][0]['pages'], [1, 2])
                preview = client.post('/api/preview/'+key, json={'method':'licensed_diagonal','page':0}, headers=headers)
                self.assertEqual(preview.status_code, 200, preview.json)
                self.assertTrue(preview.json['native_licensed'])
                options = app.settings({'method':'licensed_diagonal','auto_restore':True})
                self.assertFalse(options['auto_restore'])
                job.update(options=options, results=[], cancel=False)
                app.batch(job, options)
                self.assertEqual(job['state'], 'review', job['message'])
                self.assertEqual([x['matched'] for x in job['results']], [True, True, False])
                app.export_worker(job, [0])
                self.assertEqual(job['state'], 'done', job['message'])
                result = licensed.read_pdf(job['dir']/'cleaned.pdf')
                for i, expected in enumerate([licensed.clean_pixels(marked)[0], tall, plain]):
                    p = result.pages[i]
                    self.assertTrue(np.array_equal(licensed.native_pixels(p, licensed.scan_objects(p)), expected))
                with self.assertRaises(ValueError): app.repair_job(key, {})
                self.assertEqual(source.read_bytes(), before)
                response = client.get('/static/type-licensed-diagonal.svg')
                self.assertEqual(response.status_code, 200); response.close()
            finally:
                job['state'] = 'ready'; client.post('/api/clear/'+key, json={}, headers=headers).close()

    def test_rejects_altered_layout_and_cancelled_export_preserves_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source.pdf'; target = root/'target.pdf'
            fixture(source, [example()[1]])
            w = PdfWriter(clone_from=licensed.read_pdf(source))
            w.pages[0].rotate(90)
            self.assertIsNone(licensed.scan_objects(w.pages[0]))
            w.pages[0].rotate(-90)
            w.pages[0]['/Resources']['/XObject']['/Image1'][NameObject('/Width')] = NumberObject(2559)
            self.assertIsNone(licensed.scan_objects(w.pages[0]))
            target.write_bytes(b'previous output')
            with self.assertRaises(InterruptedError):
                licensed.write_clean(source, target, [0], root, lambda: True)
            self.assertEqual(target.read_bytes(), b'previous output')


if __name__ == '__main__': unittest.main()
