"""Tiled-border matching, protected ink and native PDF/UI integration."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import NameObject, NumberObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
import app
import nassau_scans as nassau


def fixture(frame=True, diagonal=True, sides=True):
    a = np.full((1000, 768, 3), 255, 'uint8')
    tile = np.repeat(nassau.tile_pattern()[:, :, None], 3, axis=2)
    if frame:
        for y in range(65, 970, 32):
            for x in ([67, 715] if sides else [67]):
                a[y:y+26, x:x+26] = tile
    if diagonal:
        text = np.full((100, 650), 255, 'uint8')
        cv2.putText(text, 'UNOFFICIAL COPY', (4, 70), cv2.FONT_HERSHEY_COMPLEX, 1.7, 220, 2)
        mark = Image.fromarray(text).rotate(45, expand=True, fillcolor=255)
        pixels = np.array(mark)
        h, w = pixels.shape
        region = a[250:250+h, 100:100+w]
        region[:] = np.minimum(region, pixels[:, :, None])
    a[390:395, 170:625] = 0
    a[460:462, 170:625] = 90
    a[530:534, 180:630] = [20, 40, 90]
    a[800:805, 200:600] = 60
    a[180:183, 200:250] = 210  # Faint ink outside the chosen region.
    return a


def make_pdf(path, pixels, extra_text=False):
    canvas = Canvas(str(path), pagesize=(612.5, 797.5))
    for _ in range(2):
        canvas.drawImage(ImageReader(Image.fromarray(pixels)), 0, 0, 612.5, 797.5)
        if extra_text:
            canvas.drawString(30, 30, 'Independent text must stay original')
        canvas.showPage()
    canvas.save()


class NassauTests(unittest.TestCase):
    def test_small_dense_layout_requires_both_lettering_and_frame(self):
        depth, _ = nassau.layout_profile()
        small = cv2.resize(255-depth, (430, 561), interpolation=cv2.INTER_AREA)
        scan = np.repeat(small[:, :, None], 3, axis=2)
        scan[60:495:6, 20:398] = (30, 40, 80)
        result, details = nassau.clean_pixels(scan)
        self.assertTrue(details['matched'])
        np.testing.assert_array_equal(result[60:495:6, 20:398], scan[60:495:6, 20:398])
        light = (scan.min(2) > 210) & (scan.min(2) < 245)
        self.assertGreater(np.mean(result.min(2)[light] >= 250), .95)
        frame_only = scan.copy()
        frame_only[53:529, 54:395] = 255
        words_only = np.full_like(scan, 255)
        words_only[53:529, 54:395] = scan[53:529, 54:395]
        for partial in (frame_only, words_only):
            unchanged, details = nassau.clean_pixels(partial)
            self.assertFalse(details['matched'])
            np.testing.assert_array_equal(unchanged, partial)

    def test_matching_scan_retains_dark_colored_and_outside_ink(self):
        a = fixture()
        result, info = nassau.clean_pixels(a)
        self.assertTrue(info['matched'], info)
        self.assertGreater(info['changed'], 2000)
        dark = cv2.dilate((a.min(2) < 175).astype('uint8'), np.ones((3, 3), 'uint8')) > 0
        np.testing.assert_array_equal(a[dark], result[dark])
        np.testing.assert_array_equal(a[180:183, 200:250], result[180:183, 200:250])
        self.assertTrue(info['uncertain'])

    def test_unmatched_blank_frame_only_and_diagonal_only_are_unchanged(self):
        for a in [np.full((1000, 768, 3), 255, 'uint8'), fixture(frame=False),
                  fixture(diagonal=False), fixture(sides=False), np.roll(fixture(), 150, axis=1)]:
            result, info = nassau.clean_pixels(a)
            self.assertFalse(info['matched'], info)
            np.testing.assert_array_equal(a, result)

    def test_cancelled_cleanup_does_not_continue(self):
        with self.assertRaises(InterruptedError):
            nassau.clean_pixels(fixture(), lambda: True)

    def test_partial_border_occlusion_preserves_overlapping_ink(self):
        a = fixture()
        a[40:730, 55:115] = 30
        result, info = nassau.clean_pixels(a)
        self.assertTrue(info['matched'], info)
        np.testing.assert_array_equal(a[40:730, 55:115], result[40:730, 55:115])

    def test_native_export_preserves_unselected_page_geometry_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'source.pdf'; output = root/'output.pdf'
            a = fixture(); make_pdf(source, a); original = source.read_bytes()
            preview, info = nassau.prepare(source, 0, root)
            self.assertTrue(info['matched'], info)
            nassau.write_clean(source, output, [0], root)
            p, q = PdfReader(source), PdfReader(output)
            np.testing.assert_array_equal(np.array(q.pages[0].images[0].image), nassau.clean_pixels(a)[0])
            np.testing.assert_array_equal(np.array(q.pages[1].images[0].image), a)
            for old, new in zip(p.pages, q.pages):
                self.assertEqual(old.mediabox, new.mediabox)
                self.assertEqual(old.get_contents().get_data(), new.get_contents().get_data())
            self.assertEqual(source.read_bytes(), original)
            with patch('nassau_scans.clean_pixels', side_effect=AssertionError('Cache missed')):
                self.assertEqual(nassau.prepare(source, 0, root)[0], preview)
            with self.assertRaises(InterruptedError):
                nassau.write_clean(source, root/'cancelled.pdf', [0], root, lambda: True)
            self.assertFalse((root/'cancelled.pdf').exists())

    def test_unsupported_structure_and_rotation_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'source.pdf'
            make_pdf(source, fixture(), extra_text=True)
            _, info = nassau.prepare(source, 0, root)
            self.assertFalse(info['matched'])
            page = PdfReader(source).pages[0]
            page[NameObject('/Rotate')] = NumberObject(90)
            self.assertIsNone(nassau.scan_object(page))

    def test_application_preview_batch_and_selective_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'sample.pdf'; make_pdf(source, fixture())
            client = app.app.test_client(); headers = {'X-ClearPage': 'local'}
            response = client.post('/api/upload', data=source.read_bytes(), headers=headers)
            self.assertEqual(response.status_code, 200, response.json)
            job = app.JOBS[response.json['id']]
            try:
                options = app.settings(dict(method='nassau_unofficial', mode='lama', auto_restore=True, fix_negative=True))
                self.assertFalse(options['auto_restore']); self.assertFalse(options['fix_negative'])
                self.assertEqual(options['mode'], 'careful')
                response = client.post('/api/preview/'+job['id'], json=dict(method='nassau_unofficial', page=0), headers=headers)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertTrue(response.json['matched'], response.json)
                job.update(options=options, results=[], cancel=False)
                app.batch(job, options)
                self.assertEqual(job['state'], 'review', job['message'])
                app.export_worker(job, [0])
                self.assertEqual(job['state'], 'done', job['message'])
                output = PdfReader(job['dir']/'cleaned.pdf')
                self.assertEqual(len(output.pages), 2)
                np.testing.assert_array_equal(np.array(output.pages[1].images[0].image), fixture())
                with client.get('/static/type-nassau.svg') as response:
                    self.assertEqual(response.status_code, 200)
            finally:
                job['state'] = 'ready'
                client.post('/api/clear/'+job['id'], json={}, headers=headers)


if __name__ == '__main__':
    unittest.main()
