"""Lossless compression, content preservation and authorized application routes."""
from io import BytesIO
from pathlib import Path
import hashlib
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import pikepdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

import app
import licensing
import pdf_compression


def fixture(path):
    image = Image.fromarray(np.random.default_rng(5).integers(0, 256, (140, 120, 3), dtype='uint8'))
    jpeg = BytesIO()
    image.save(jpeg, format='JPEG', quality=94)
    canvas = Canvas(str(path), pagesize=(300, 400), pageCompression=0, invariant=1)
    for index in range(3):
        canvas.drawString(30, 360, f'Original text {index + 1} - 12345')
        canvas.drawImage(ImageReader(jpeg), 30, 170, 120, 140)
        canvas.drawImage(ImageReader(image), 165, 170, 120, 140)
        for row in range(100):
            canvas.line(30, row, 250, row)
        canvas.linkURL('https://example.test/document', (30, 350, 200, 370))
        canvas.showPage()
    canvas.save()
    writer = PdfWriter(clone_from=path)
    writer.pages[1].rotate(90)
    writer.pages[2].cropbox.lower_left = (10, 20)
    writer.add_outline_item('Document start', 0)
    writer.add_attachment('note.txt', b'Keep this attachment exactly.')
    writer.add_metadata({'/Title': 'Compression preservation fixture'})
    writer.write(path)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'original.pdf'
        self.output = self.root/'compressed.pdf'
        fixture(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_rendered_pixels_text_images_links_and_attachments_are_preserved(self):
        original_digest = digest(self.source)
        result = pdf_compression.compress_pdf(self.source, self.output)
        self.assertTrue(result['reduced'])
        self.assertTrue(result['target_met'])
        self.assertEqual(digest(self.source), original_digest)
        before, after = PdfReader(self.source), PdfReader(self.output)
        self.assertEqual(len(before.pages), len(after.pages))
        self.assertEqual(before.metadata.title, after.metadata.title)
        self.assertEqual(before.attachments['note.txt'], after.attachments['note.txt'])
        self.assertEqual(before.outline[0].title, after.outline[0].title)
        for index, (left, right) in enumerate(zip(before.pages, after.pages)):
            self.assertEqual(left.extract_text(), right.extract_text())
            self.assertEqual(list(left.mediabox), list(right.mediabox))
            self.assertEqual(list(left.cropbox), list(right.cropbox))
            self.assertEqual(left.rotation, right.rotation)
            self.assertEqual(left['/Annots'][0].get_object()['/A']['/URI'],
                             right['/Annots'][0].get_object()['/A']['/URI'])
            left_images = sorted((img.image.size, img.image.mode, img.image.tobytes()) for img in left.images)
            right_images = sorted((img.image.size, img.image.mode, img.image.tobytes()) for img in right.images)
            self.assertEqual(left_images, right_images)
            np.testing.assert_array_equal(np.asarray(app.render_page(self.source, index)),
                                          np.asarray(app.render_page(self.output, index)))

    def test_exact_decimal_target_boundary_and_no_growth(self):
        pdf_compression.compress_pdf(self.source, self.output)
        optimized = self.root/'optimized.pdf'
        optimized.write_bytes(self.output.read_bytes())
        size = optimized.stat().st_size
        with patch.object(pdf_compression, 'TARGET_BYTES', size):
            result = pdf_compression.compress_pdf(optimized, self.output)
        self.assertTrue(result['target_met'])
        self.assertLessEqual(result['output_bytes'], size)
        with patch.object(pdf_compression, 'TARGET_BYTES', 1):
            result = pdf_compression.compress_pdf(optimized, self.output)
        self.assertFalse(result['target_met'])
        self.assertFalse(result['reduced'])
        self.assertEqual(optimized.read_bytes(), self.output.read_bytes())

    def test_uncompressible_document_above_25_mb_reports_target_unmet(self):
        with pikepdf.Pdf.open(self.source) as pdf:
            pdf.attachments['scan-data.bin'] = os.urandom(25_000_001)
            pdf.save(self.root/'large.pdf')
        result = pdf_compression.compress_pdf(self.root/'large.pdf', self.output)
        self.assertGreater(result['output_bytes'], 25_000_000)
        self.assertFalse(result['target_met'])
        self.assertLessEqual(result['output_bytes'], result['original_bytes'])

    def test_cancellation_and_failed_publish_preserve_existing_files(self):
        original_digest = digest(self.source)
        self.output.write_bytes(b'previous completed output')
        cancelled = [False]
        def progress(value):
            cancelled[0] = True
        with self.assertRaises(InterruptedError):
            pdf_compression.compress_pdf(self.source, self.output, lambda: cancelled[0], progress)
        self.assertEqual(self.output.read_bytes(), b'previous completed output')
        self.assertFalse(self.output.with_suffix('.part.pdf').exists())
        with patch.object(pdf_compression.os, 'replace', side_effect=OSError('test disk failure')):
            with self.assertRaises(OSError):
                pdf_compression.compress_pdf(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'previous completed output')
        self.assertEqual(digest(self.source), original_digest)
        self.assertFalse(self.output.with_suffix('.part.pdf').exists())

    def test_original_signed_encrypted_and_invalid_inputs_are_protected(self):
        with self.assertRaises(ValueError):
            pdf_compression.compress_pdf(self.source, self.source)
        writer = PdfWriter(clone_from=self.source)
        writer.encrypt('private-test-only')
        writer.write(self.root/'encrypted.pdf')
        with self.assertRaises(ValueError):
            pdf_compression.compress_pdf(self.root/'encrypted.pdf', self.output)
        writer = PdfWriter(clone_from=self.source)
        writer.root_object[NameObject('/TestSignature')] = DictionaryObject({NameObject('/Type'): NameObject('/Sig')})
        writer.write(self.root/'signed.pdf')
        with self.assertRaisesRegex(ValueError, 'signature'):
            pdf_compression.compress_pdf(self.root/'signed.pdf', self.output)
        (self.root/'invalid.pdf').write_bytes(b'%PDF-invalid')
        with self.assertRaises(ValueError):
            pdf_compression.compress_pdf(self.root/'invalid.pdf', self.output)
        self.assertFalse(self.output.exists())


class CompressionApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.headers = {'X-ClearPage': 'local', 'X-Filename': 'compression.pdf'}
        self.temp = tempfile.TemporaryDirectory()
        source = Path(self.temp.name)/'upload.pdf'
        fixture(source)
        response = self.client.post('/api/upload', data=source.read_bytes(), headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.key = response.json['id']
        self.job = app.JOBS[self.key]

    def tearDown(self):
        self.job['state'] = 'ready'
        self.client.post('/api/clear/'+self.key, json={}, headers=self.headers)
        self.temp.cleanup()

    def wait(self):
        for _ in range(300):
            with app.LOCK:
                result = self.client.get('/api/compress/'+self.key).json
                if self.job['state'] != 'exporting':
                    return result
            time.sleep(.02)
        self.fail('Compression did not finish')

    def test_original_download_retries_and_cleaned_export_are_separate(self):
        before = self.job['source'].read_bytes()
        self.assertEqual(self.client.get('/api/compressed-download/'+self.key).status_code, 400)
        for _ in range(2):
            response = self.client.post('/api/compress/'+self.key, json={'source': 'original'}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.json)
            result = self.wait()
            self.assertEqual(result['status'], 'ready', result)
            self.assertEqual(self.job['state'], 'ready')
            with self.client.get('/api/compressed-download/'+self.key) as download:
                self.assertEqual(download.status_code, 200)
                self.assertEqual(len(download.data), result['output_bytes'])
                self.assertIn('compressed.pdf', download.headers['Content-Disposition'])
        self.assertEqual(self.job['source'].read_bytes(), before)
        self.assertEqual(self.job['results'], [])
        response = self.client.post('/api/compress/'+self.key, json={'source': 'cleaned'}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        writer = PdfWriter(clone_from=self.job['source'])
        writer.add_metadata({'/Title': 'Reviewed cleanup output'})
        writer.write(self.job['dir']/'cleaned.pdf')
        self.job.update(state='done', results=[{'page': 0, 'changed': 1}])
        response = self.client.post('/api/compress/'+self.key, json={'source': 'cleaned'}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.wait()['source'], 'cleaned')
        self.assertEqual(self.job['state'], 'done')
        self.assertEqual(PdfReader(self.job['dir']/'compressed.pdf').metadata.title, 'Reviewed cleanup output')

    def test_busy_cancellation_failure_recovery_and_download_gating(self):
        entered, release = threading.Event(), threading.Event()
        def controlled(source, target, cancelled, progress):
            entered.set()
            release.wait(5)
            if cancelled():
                raise InterruptedError()
            raise ValueError('Synthetic verification failure')
        with patch.object(pdf_compression, 'compress_pdf', side_effect=controlled):
            response = self.client.post('/api/compress/'+self.key, json={}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.client.post('/api/compress/'+self.key, json={}, headers=self.headers).status_code, 400)
            self.assertEqual(self.client.post('/api/clear/'+self.key, json={}, headers=self.headers).status_code, 400)
            self.assertEqual(self.client.get('/api/compressed-download/'+self.key).status_code, 400)
            self.assertEqual(self.client.post('/api/cancel/'+self.key, json={}, headers=self.headers).status_code, 200)
            release.set()
            self.assertEqual(self.wait()['status'], 'cancelled')
            self.assertEqual(self.job['state'], 'ready')
            self.client.post('/api/compress/'+self.key, json={}, headers=self.headers)
            self.assertEqual(self.wait()['status'], 'error')
            self.assertEqual(self.job['state'], 'ready')

    def test_invalid_requests_and_payment_authorization(self):
        for body in ([], {'source': '../original.pdf'}, {'source': True}):
            self.assertEqual(self.client.post('/api/compress/'+self.key, json=body, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/compress/'+self.key, json={}).status_code, 403)
        with patch.object(app.BILLING, 'document', side_effect=licensing.LicenseRequired('Paid access required')):
            self.assertEqual(self.client.post('/api/compress/'+self.key, json={}, headers=self.headers).status_code, 402)
            self.assertEqual(self.client.get('/api/compress/'+self.key).status_code, 402)
            self.assertEqual(self.client.get('/api/compressed-download/'+self.key).status_code, 402)


if __name__ == '__main__':
    unittest.main()
