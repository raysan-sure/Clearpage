"""Kane outline detection, ink protection, and complete application routing."""
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from pypdf import PdfReader
import app
from clearpage.presets import kane_outline as kane
from tests.test_screened_scans import fixture


def example():
    ink = np.zeros(kane.SHAPE, 'uint8')
    for y in range(630, 1500, 45):
        cv2.putText(ink, 'Original words and signatures', (350, y), cv2.FONT_HERSHEY_SIMPLEX, 1, 1, 2)
    ink[90:94, 100:320] = 1
    ink[100, 450] = 1
    ink = ink.astype(bool)
    return ink, ink | kane.profile()[0]


class KaneOutlineTests(unittest.TestCase):
    def test_blank_watermark_fully_removed_and_source_unchanged(self):
        mask, _ = kane.profile()
        source = mask.copy()
        out, info = kane.clean_pixels(source)
        self.assertTrue(info['matched'])
        self.assertEqual(info['changed'], 6063)
        self.assertFalse(out.any())
        self.assertTrue(np.array_equal(source, mask))

    def test_ink_preserved_outside_dots_and_blank_contours_gone(self):
        ink, scan = example()
        out, info = kane.clean_pixels(scan)
        mask, _ = kane.profile()
        self.assertTrue(info['uncertain'])
        self.assertTrue(np.array_equal(out[~mask], ink[~mask]))
        blank = cv2.dilate(ink.astype('uint8'), np.ones((9, 9), 'uint8')) == 0
        self.assertFalse(out[blank].any())
        # Most known crossing ink survives the local interpolation.
        self.assertGreater(np.mean(out[ink & mask]), .8)

    def test_unmatched_shifted_resized_and_solid_pages_untouched(self):
        ink, scan = example()
        for unsupported in (ink, np.roll(scan, 1, axis=0), scan[::2, ::2], np.ones(kane.SHAPE, bool)):
            out, info = kane.clean_pixels(unsupported)
            self.assertFalse(info['matched'])
            self.assertTrue(np.array_equal(unsupported, out))
        with self.assertRaises(InterruptedError):
            kane.clean_pixels(scan, lambda: True)

    def test_application_suggest_preview_batch_and_selected_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'synthetic.pdf'
            ink, scan = example()
            fixture(source, [scan, scan, ink], tiles=1)
            before = source.read_bytes()
            client = app.app.test_client()
            client.get('/').close()
            headers = {'X-ClearPage': 'local', 'X-Filename': 'synthetic.pdf'}
            uploaded = client.post('/api/upload', data=before, headers=headers)
            self.assertEqual(uploaded.status_code, 200, uploaded.json)
            key = uploaded.json['id']; job = app.JOBS[key]
            try:
                suggestion = client.get('/api/suggestions/'+key).json
                self.assertEqual(suggestion['items'][0]['method'], 'kane_outline')
                self.assertEqual(suggestion['items'][0]['pages'], [1, 2])
                preview = client.post('/api/preview/'+key, json={'method': 'kane_outline', 'page': 0}, headers=headers)
                self.assertEqual(preview.status_code, 200, preview.json)
                self.assertTrue(preview.json['native_kane'])
                options = app.settings({'method': 'kane_outline'})
                job.update(options=options, results=[], cancel=False)
                app.batch(job, options)
                self.assertEqual(job['state'], 'review', job['message'])
                self.assertEqual([x['matched'] for x in job['results']], [True, True, False])
                for keep in ([0], [0, 1]):
                    app.export_worker(job, keep)
                    self.assertEqual(job['state'], 'done', job['message'])
                    result = PdfReader(job['dir']/'cleaned.pdf')
                    self.assertEqual(len(result.pages), 3)
                    for i, (p, q) in enumerate(zip(PdfReader(source).pages, result.pages)):
                        expected = kane.clean_pixels(scan)[0] if i in keep else (ink if i == 2 else scan)
                        self.assertTrue(np.array_equal(kane.native_pixels(q, kane.scan_objects(q)), expected))
                        self.assertEqual(p.get_contents().get_data(), q.get_contents().get_data())
                        self.assertEqual(p.mediabox, q.mediabox)
                self.assertEqual(source.read_bytes(), before)
                with self.assertRaises(ValueError):
                    app.repair_job(key, {})
                image = client.get('/static/type-kane-outline.svg')
                self.assertEqual(image.status_code, 200); image.close()
            finally:
                job['state'] = 'ready'
                client.post('/api/clear/'+key, json={}, headers=headers).close()


if __name__ == '__main__':
    unittest.main()
