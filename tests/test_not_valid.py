"""Synthetic NOT VALID fixtures; no customer document pixels are committed."""
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
import zlib

import cv2
import numpy as np
import pikepdf
from PIL import Image, ImageDraw

from clearpage.presets import not_valid


def fixture(width=1272, height=1682):
    mask, _ = not_valid.pattern()
    rgb = np.full((height, width, 3), 255, 'uint8')
    h, w = min(height, mask.shape[0]), min(width, mask.shape[1])
    rgb[:h, :w][mask[:h, :w] > 0] = 162
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    for y in range(180, min(height-100, 1500), 45):
        draw.text((170, y), 'SYNTHETIC DOCUMENT 12345 - unchanged original ink', fill=(0, 0, 0))
    draw.line((80, 480, min(width-10, 800), 480), fill=(50, 50, 50), width=2)
    draw.rectangle((160, 400, 185, 425), fill=(160, 0, 0))
    return np.array(image)


def write_fixture(path, pages, jpeg=False):
    with pikepdf.new() as doc:
        for rgb in pages:
            h, w = rgb.shape[:2]
            if jpeg:
                encoded = BytesIO()
                Image.fromarray(rgb).save(encoded, 'JPEG', quality=94)
                data, codec = encoded.getvalue(), pikepdf.Name.DCTDecode
            else:
                data, codec = zlib.compress(rgb.tobytes()), pikepdf.Name.FlateDecode
            image = doc.make_stream(data)
            image.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                Width=w, Height=h, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceRGB, Filter=codec))
            page = doc.add_blank_page(page_size=(w, h))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=image))
            page.Contents = doc.make_stream(f'q {w} 0 0 {h} 0 0 cm /Scan Do Q'.encode())
        doc.save(path)


class NotValidTests(unittest.TestCase):
    def test_supported_sizes_preserve_dark_ink_colors_and_outside_pixels(self):
        for w, h in ((1272, 1682), (1054, 1878), (958, 1187), (1273, 2114)):
            with self.subTest(size=(w, h)):
                rgb = fixture(w, h)
                scope, _ = not_valid.locate(rgb)
                cleaned, details = not_valid.clean_pixels(rgb)
                self.assertTrue(details['matched'])
                self.assertGreater(details['changed'], 30000)
                gray = rgb.min(2)
                np.testing.assert_array_equal(cleaned[gray <= 75], rgb[gray <= 75])
                np.testing.assert_array_equal(cleaned[~scope], rgb[~scope])
                np.testing.assert_array_equal(cleaned[np.ptp(rgb, axis=2) > 12], rgb[np.ptp(rgb, axis=2) > 12])
                stamp_tone = np.all(rgb == 162, axis=2)
                self.assertLess(np.count_nonzero(cleaned[stamp_tone].min(1) < 230), stamp_tone.sum()*.01)

    def test_blank_unrelated_and_partial_stamps_are_rejected(self):
        blank = np.full((1682, 1272, 3), 255, 'uint8')
        gray_page = np.full_like(blank, 162)
        stripe = blank.copy()
        cv2.line(stripe, (30, 80), (1100, 1550), (162, 162, 162), 90)
        fragment = fixture()
        fragment[500:] = 255
        for rgb in (blank, gray_page, stripe, fragment):
            with self.subTest(kind=int(rgb.sum())):
                cleaned, details = not_valid.clean_pixels(rgb)
                self.assertFalse(details['matched'])
                np.testing.assert_array_equal(cleaned, rgb)

    def test_jpeg_opaque_alpha_and_transparency_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'jpeg.pdf'
            write_fixture(path, [fixture()], jpeg=True)
            with pikepdf.open(path) as doc:
                page = doc.pages[0]
                name, rgb = not_valid.scan(page)
                self.assertTrue(not_valid.clean_pixels(rgb)[1]['matched'])
                alpha = doc.make_stream(b'\xff'*(1272*1682))
                alpha.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                    Width=1272, Height=1682, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceGray))
                page.Resources.XObject[name].SMask = alpha
                self.assertIsNotNone(not_valid.scan(page))
                alpha.write(b'\x00'+b'\xff'*(1272*1682-1))
                self.assertIsNone(not_valid.scan(page))

    def test_cached_selective_export_preserves_original_geometry_and_unchecked_streams(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, target = folder/'source.pdf', folder/'output.pdf'
            write_fixture(source, [fixture(), fixture()])
            with pikepdf.open(source, allow_overwriting_input=True) as doc:
                doc.pages[0].Rotate = 90
                doc.pages[0].CropBox = pikepdf.Array([20, 30, 1200, 1600])
                doc.save(source)
            original = source.read_bytes()
            preview, details = not_valid.prepare(source, 0, folder)
            timestamp = preview.stat().st_mtime_ns
            self.assertTrue(details['matched'])
            self.assertEqual(not_valid.prepare(source, 0, folder)[1], details)
            self.assertEqual(preview.stat().st_mtime_ns, timestamp)
            not_valid.write_clean(source, target, [0], folder)
            self.assertEqual(source.read_bytes(), original)
            with pikepdf.open(source) as a, pikepdf.open(target) as b, pikepdf.open(preview) as p:
                self.assertEqual(len(b.pages), 2)
                for x, y in zip(a.pages, b.pages):
                    self.assertEqual(list(x.mediabox), list(y.mediabox))
                    self.assertEqual(list(x.cropbox), list(y.cropbox))
                    self.assertEqual(x.get('/Rotate', 0), y.get('/Rotate', 0))
                self.assertEqual(a.pages[1].Resources.XObject.Scan.read_raw_bytes(), b.pages[1].Resources.XObject.Scan.read_raw_bytes())
                self.assertEqual(p.pages[0].Resources.XObject.Scan.read_bytes(), b.pages[0].Resources.XObject.Scan.read_bytes())

    def test_native_preview_composes_additional_marks_and_cancellation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder/'source.pdf'
            rgb = fixture()
            write_fixture(path, [rgb])
            np.testing.assert_array_equal(np.asarray(not_valid.display_image(path, 0)), rgb)
            with pikepdf.open(path, allow_overwriting_input=True) as doc:
                raw = doc.pages[0].Contents.read_bytes()+b' 1 0 0 rg 30 30 20 20 re f'
                doc.pages[0].Contents = doc.make_stream(raw)
                doc.save(path)
            composed = not_valid.display_image(path, 0)
            self.assertEqual(composed.getpixel((40, 1682-40)), (255, 0, 0))
            with self.assertRaises(InterruptedError):
                not_valid.prepare(path, 0, folder, lambda: True)
            self.assertFalse(list(folder.glob('*not-valid*')))

    def test_application_suggestions_preview_batch_and_selected_export(self):
        import app
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder/'synthetic.pdf'
            write_fixture(source, [fixture(), np.full((1682, 1272, 3), 255, 'uint8'), fixture(1273, 2114)])
            client = app.app.test_client()
            headers = {'X-ClearPage': 'local', 'X-Filename': 'synthetic.pdf'}
            response = client.post('/api/upload', data=source.read_bytes(), headers=headers)
            self.assertEqual(response.status_code, 200, response.json)
            key = response.json['id']
            def wait(state):
                deadline = time.monotonic()+30
                while time.monotonic() < deadline:
                    job = client.get('/api/job/'+key).json
                    if job['state'] not in ('processing', 'exporting'):
                        self.assertEqual(job['state'], state, job.get('message'))
                        return job
                    time.sleep(.02)
                self.fail('Synthetic NOT VALID workflow timed out')
            try:
                original = app.JOBS[key]['source'].read_bytes()
                suggestion = client.get('/api/suggestions/'+key).json
                self.assertEqual(suggestion['items'][0]['method'], 'not_valid')
                self.assertEqual(suggestion['items'][0]['pages'], [1, 3])
                options = dict(method='not_valid', page=0)
                preview = client.post('/api/preview/'+key, json=options, headers=headers)
                self.assertEqual(preview.status_code, 200, preview.json)
                self.assertTrue(preview.json['matched'])
                with client.get(f'/api/page/{key}/0/after') as image:
                    self.assertEqual(Image.open(BytesIO(image.data)).size, (1272, 1682))
                self.assertEqual(client.post('/api/process/'+key, json=options, headers=headers).status_code, 200)
                job = wait('review')
                self.assertEqual([r['matched'] for r in job['results']], [True, False, True])
                self.assertEqual(client.post('/api/export/'+key, json=dict(keep=[0]), headers=headers).status_code, 200)
                wait('done')
                with client.get('/api/download/'+key) as response:
                    self.assertEqual(response.status_code, 200)
                    with pikepdf.open(BytesIO(response.data)) as result, pikepdf.open(source) as before:
                        self.assertEqual(len(result.pages), 3)
                        self.assertEqual(result.pages[2].Resources.XObject.Scan.read_raw_bytes(), before.pages[2].Resources.XObject.Scan.read_raw_bytes())
                self.assertEqual(app.JOBS[key]['source'].read_bytes(), original)
            finally:
                client.post('/api/clear/'+key, json={}, headers=headers)
