"""Render public-route isolation with a disposable provider fixture."""
import os
from unittest.mock import patch
import unittest
from tests import test_razorpay
from payment_service.hosted import hosted_app


class HostingTests(unittest.TestCase):
    def test_only_public_assets_and_named_archive_are_exposed(self):
        fixture = test_razorpay.RazorpayTests()
        fixture.setUp()
        try:
            with patch.dict(os.environ, {**fixture.settings, 'CLEARPAGE_DOWNLOAD_DIR':str(fixture.root/'downloads')}):
                client = hosted_app().test_client()
                for path in ('/','/policies.html','/style.css','/enquiry.js','/download.js','/health'):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, path)
                    response.close()
                for path in ('/.env','/settings.json','/credits.sqlite3','/downloads/settings.json'):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 404, path)
                    response.close()
                response = client.get('/downloads/ClearPage-Windows-x64.zip')
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers['Location'],'https://github.com/invicto-labs/Watermark-remover/releases/latest/download/ClearPage-Windows-x64.zip')
                self.assertEqual(response.headers['Cache-Control'],'no-store')
                response.close()
                (fixture.root/'downloads').mkdir()
                (fixture.root/'downloads/ClearPage-Windows-x64.zip').write_bytes(b'test-archive')
                response = client.get('/downloads/ClearPage-Windows-x64.zip')
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response.data, b'test-archive')
                response.close()
        finally:
            fixture.tearDown()
