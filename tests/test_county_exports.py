"""Preview and selectively export synthetic scans matching the bundled county profiles."""
from io import BytesIO
import unittest
import zlib

import cv2
import numpy as np
import pikepdf
from PIL import Image

import app
from clearpage.paths import RESOURCE_ROOT


def scan_pdf(scans, jpeg=False):
    buffer = BytesIO()
    with pikepdf.new() as doc:
        for pixels in scans:
            height, width = pixels.shape[:2]
            if jpeg:
                encoded = BytesIO()
                Image.fromarray(pixels).save(encoded, format='JPEG', quality=98, subsampling=0)
                stream = doc.make_stream(encoded.getvalue())
            else:
                stream = doc.make_stream(zlib.compress(pixels.tobytes()))
            stream.update(pikepdf.Dictionary(
                Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                Filter=pikepdf.Name.DCTDecode if jpeg else pikepdf.Name.FlateDecode,
                Width=width, Height=height, ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8))
            page = doc.add_blank_page(page_size=(300, 400))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=stream))
            page.Contents = doc.make_stream(b'q 300 0 0 400 0 0 cm /Scan Do Q')
        doc.save(buffer)
    return buffer.getvalue()


class CountyExportTests(unittest.TestCase):
    def check_cleanup(self, method, source):
        client = app.app.test_client()
        headers = {'X-ClearPage': 'local', 'X-Filename': 'county.pdf'}
        response = client.post('/api/upload', data=source, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        key = response.json['id']
        self.addCleanup(client.post, '/api/clear/'+key, json={}, headers=headers)
        response = client.post('/api/preview/'+key, json={'method': method, 'page': 0}, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['matched'], response.json)
        job = app.JOBS[key]
        options = app.settings({'method': method})
        job.update(options=options, cancel=False, results=[])
        app.batch(job, options)
        self.assertEqual(job['state'], 'review', job['message'])
        self.assertTrue(all(row['matched'] for row in job['results']), job['results'])
        app.export_worker(job, [0])
        self.assertEqual(job['state'], 'done', job['message'])
        with client.get('/api/download/'+key) as download:
            self.assertEqual(download.status_code, 200)
            exported = download.data
        with pikepdf.open(BytesIO(source)) as original, pikepdf.open(BytesIO(exported)) as output:
            self.assertEqual(len(output.pages), 2)
            for index, (page, cleaned) in enumerate(zip(original.pages, output.pages, strict=True)):
                self.assertEqual(page.MediaBox, cleaned.MediaBox)
                before = self.pixels(page)
                after = self.pixels(cleaned)
                self.assertEqual(before.shape, after.shape)
                if index == 0:
                    self.assertLess(np.ptp(after, axis=2).sum(), np.ptp(before, axis=2).sum())
                    np.testing.assert_array_equal(after[30:45, 20:40], before[30:45, 20:40])
                else:
                    np.testing.assert_array_equal(after, before)

    @staticmethod
    def pixels(page):
        stream = page.Resources.XObject.Scan
        if stream.Filter == '/DCTDecode':
            # Decode the embedded JPEG directly to avoid an extra encoding pass.
            with Image.open(BytesIO(stream.read_raw_bytes())) as image:
                return np.array(image.convert('RGB'))
        with pikepdf.PdfImage(stream).as_pil_image() as image:
            return np.array(image.convert('RGB'))

    def test_henry_red_needs_two_distinct_stamped_scans(self):
        with Image.open(RESOURCE_ROOT/'static/type-henry-red.webp') as image:
            mark = np.asarray(image.convert('RGB'))[:, :, 1] < 233
        mask = cv2.resize(mark.astype('uint8'), (750, 978), interpolation=cv2.INTER_NEAREST).astype(bool)
        first = np.full((978, 750, 3), 255, 'uint8')
        first[mask] = (255, 200, 200)
        first[30:45, 20:40] = 0
        second = first.copy()
        second[200:260, 50:180] = 0
        self.check_cleanup('henry_red', scan_pdf([first, second], jpeg=True))

    def test_union_parish_three_lavender_stamps(self):
        with Image.open(RESOURCE_ROOT/'static/type-union-parish.webp') as image:
            mark = np.asarray(image.convert('RGB'))[:, :, 0] < 225
        rows, cols = np.nonzero(mark)
        word = mark[rows.min():rows.max()+1, cols.min():cols.max()+1]
        h, w = word.shape
        pixels = np.full((1000, 750, 3), 255, 'uint8')
        pixels[30:45, 20:40] = 0
        for center in (63, 503, 942):
            y, x = center-h//2, (750-w)//2
            pixels[y:y+h, x:x+w][word] = (195, 195, 255)
        self.check_cleanup('union_parish', scan_pdf([pixels, pixels]))

    def test_cumberland_registered_red_profile(self):
        with np.load(RESOURCE_ROOT/'assets/cumberland-red.npz') as profile:
            green = cv2.resize(profile['green'], (767, 992), interpolation=cv2.INTER_AREA)
            chroma = cv2.resize(profile['chroma'], (767, 992), interpolation=cv2.INTER_AREA)
        red = cv2.add(green, chroma)
        pixels = np.stack((red, green, green), axis=2)
        pixels[30:45, 20:40] = 0
        self.check_cleanup('cumberland_red', scan_pdf([pixels, pixels]))
