"""No external API calls: protocol, privacy, failure, and real application export tests."""
import base64
import io
import json
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
from PIL import Image, ImageDraw
import numpy as np
import app
from clearpage.pdf import engine
from clearpage.restore import gemini_cleanup as gemini

KEY = 'AQ.synthetic-unit-test-credential'
HEADERS = {'X-ClearPage': 'local'}


def encoded(image):
    data = io.BytesIO(); image.save(data, format='PNG')
    return base64.b64encode(data.getvalue()).decode('ascii')


def reply(image):
    return json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
        {'inlineData': {'mimeType': 'image/png', 'data': encoded(image)}}]}}]}).encode()


class GeminiTests(unittest.TestCase):
    def setUp(self):
        gemini.configure('')
        self.client = app.app.test_client()
        self.image = Image.new('RGB', (400, 600), 'white')
        ImageDraw.Draw(self.image).text((30, 40), 'Original 12345', fill='black')
        self.jobs = []

    def tearDown(self):
        gemini.configure('')
        for job in self.jobs:
            job['state'] = 'ready'
            self.client.post('/api/clear/' + job['id'], json={}, headers=HEADERS)

    def upload(self):
        # Multipage input makes preserving unchecked pages an actual regression check.
        data = io.BytesIO(); self.image.save(data, format='TIFF', save_all=True, append_images=[self.image])
        response = self.client.post('/api/upload', data=data.getvalue(), headers={**HEADERS, 'X-Filename': 'trial.tiff'})
        self.assertEqual(response.status_code, 200, response.json)
        job = app.JOBS[response.json['id']]; self.jobs.append(job)
        return job

    def test_credentials_are_local_only_and_not_returned(self):
        for invalid in (KEY + ' ', KEY + '\r\nExtra: header'):
            response = self.client.post('/api/gemini/key', json={'key': invalid}, headers=HEADERS)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(gemini.status()['configured'])
        response = self.client.post('/api/gemini/key', json={'key': KEY})
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/gemini/key', json={'key': KEY}, headers={**HEADERS, 'Origin': 'https://example.org'})
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/gemini/key', json={'key': KEY}, headers=HEADERS)
        self.assertTrue(response.json['configured'])
        self.assertNotIn(KEY, response.text)
        self.assertNotIn(KEY, self.client.get('/api/gemini').text)
        self.client.post('/api/gemini/key', json={'key': ''}, headers=HEADERS)
        self.assertFalse(gemini.status()['configured'])

    def test_protocol_dimensions_and_no_secret_in_url_or_payload(self):
        with patch.object(gemini.urllib.request, 'build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(reply(self.image.resize((800, 1200))))
            result, meta = gemini.reconstruct(self.image, KEY)
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, gemini.ENDPOINT)
            self.assertEqual(request.get_header('X-goog-api-key'), KEY)
            self.assertNotIn(KEY, request.full_url)
            self.assertNotIn(KEY.encode(), request.data)
            self.assertEqual(result.size, self.image.size)
            self.assertEqual(meta['generated_size'], [800, 1200])
            payload = json.loads(request.data)
            self.assertEqual(len(payload['contents'][0]['parts']), 2)
            self.assertEqual(factory.return_value.open.call_count, 1)
        self.assertIsNone(gemini.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org'))

    def test_invalid_outputs_and_remote_errors_are_safe(self):
        for body in (b'{}', b'invalid', reply(Image.new('RGB', (800, 400))),
                     json.dumps({'candidates': [{'finishReason': 'MAX_TOKENS'}]}).encode()):
            with patch.object(gemini.urllib.request, 'build_opener') as factory:
                factory.return_value.open.return_value = io.BytesIO(body)
                with self.assertRaisesRegex(ValueError, 'valid full-page'):
                    gemini.reconstruct(self.image, KEY)
        with patch.object(gemini.urllib.request, 'build_opener') as factory:
            factory.return_value.open.side_effect = urllib.error.HTTPError(gemini.ENDPOINT, 429, KEY, {}, io.BytesIO(KEY.encode()))
            with self.assertRaises(ValueError) as caught:
                gemini.reconstruct(self.image, KEY)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertIn('quota', str(caught.exception))
            self.assertEqual(factory.return_value.open.call_count, 1)

    def test_explicit_page_consent_key_required_and_no_queue_fallback(self):
        job = self.upload(); route = '/api/gemini/trial/' + job['id']
        for payload in ({'page': 0}, {'page': 100, 'consent': True}, {'page': True, 'consent': True}, {'page': 0, 'consent': True}):
            response = self.client.post(route, json=payload, headers=HEADERS)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(job['state'], 'ready')
        with self.assertRaises(ValueError): app.settings({'method': 'gemini_draft'})

    def test_trial_cache_review_gate_and_complete_pdf_export(self):
        job = self.upload(); source_bytes = job['source'].read_bytes()
        gemini.configure(KEY)
        def reconstruct(image, key, cancelled):
            self.assertEqual(key, KEY)
            result = image.copy(); ImageDraw.Draw(result).rectangle((20, 20, 60, 60), fill='gray')
            return result, dict(model=gemini.MODEL, source_size=list(image.size), generated_size=list(image.size))
        with patch.object(gemini, 'reconstruct', side_effect=reconstruct) as remote:
            for attempt in range(2):
                response = self.client.post('/api/gemini/trial/' + job['id'], json={'page': 1, 'consent': True}, headers=HEADERS)
                self.assertEqual(response.status_code, 200, response.json)
                deadline = time.monotonic() + 10
                while job['state'] == 'processing' and time.monotonic() < deadline: time.sleep(.02)
                self.assertEqual(job['state'], 'review', job['message'])
            self.assertEqual(remote.call_count, 1)
        self.assertEqual(len(job['results']), 2)
        self.assertEqual(job['results'][0]['changed'], 0)
        self.assertTrue(job['results'][1]['reconstruction_unverified'])
        self.assertNotIn(KEY, json.dumps(app.public(job)))
        with self.assertRaises(ValueError): app.require_ai_review(job, [1])
        app.require_ai_review(job, [])
        self.client.post('/api/view/' + job['id'], json={'page': 1, 'keep': [1], 'reviewed': [1]}, headers=HEADERS)
        app.export_worker(job, [1])
        self.assertEqual(job['state'], 'done', job['message'])
        self.assertEqual(engine.inspect_pdf(job['dir']/'cleaned.pdf')[0], 2)
        np.testing.assert_array_equal(engine.render_page(job['dir']/'cleaned.pdf', 0), engine.render_page(job['source'], 0))
        np.testing.assert_array_equal(engine.render_page(job['dir']/'cleaned.pdf', 1), Image.open(app.image_path(job, 1, True)))
        self.assertEqual(job['source'].read_bytes(), source_bytes)

    def test_cancellation_keeps_polling_available_and_discards_draft(self):
        job = self.upload(); gemini.configure(KEY)
        started = threading.Event(); release = threading.Event()
        def slow(image, key, cancelled):
            started.set(); release.wait(5)
            if cancelled(): raise InterruptedError()
            return image, {}
        with patch.object(gemini, 'reconstruct', side_effect=slow):
            self.client.post('/api/gemini/trial/' + job['id'], json={'page': 0, 'consent': True}, headers=HEADERS)
            self.assertTrue(started.wait(5))
            try:
                self.assertTrue(app.LOCK.acquire(timeout=.5))
                app.LOCK.release()
                self.assertEqual(self.client.get('/api/job/' + job['id']).json['state'], 'processing')
                self.client.post('/api/cancel/' + job['id'], json={}, headers=HEADERS)
            finally: release.set()
            deadline = time.monotonic() + 5
            while job['state'] == 'processing' and time.monotonic() < deadline: time.sleep(.02)
            self.assertEqual(job['state'], 'cancelled')
            self.assertFalse(app.image_path(job, 0, True).exists())


if __name__ == '__main__': unittest.main()
