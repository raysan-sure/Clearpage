from io import BytesIO
import json
import unittest
from unittest.mock import patch
import urllib.error
from clearpage import updates
from clearpage import workspace


class UpdateTests(unittest.TestCase):
    def setUp(self): updates._cache.clear()
    def tearDown(self): updates._cache.clear()
    def release(self, value='99.0', **changes):
        package=f'ClearPage-{value}-Windows-x64.zip'
        item=dict(tag_name='v'+value,draft=False,prerelease=False,body='New pattern support.',assets=[dict(name=package,state='uploaded',size=100,browser_download_url=f'https://github.com/{updates.REPOSITORY}/releases/download/v{value}/{package}')])
        item.update(changes);return item
    def test_notifies_only_newer_stable_with_uploaded_windows_asset(self):
        self.assertTrue(updates.describe(self.release())['available'])
        for release in (self.release(updates.VERSION),self.release('0.1'),self.release(draft=True),
                        self.release(prerelease=True),self.release(assets=[]),self.release(assets=None),
                        self.release(tag_name='v99.0-beta'),self.release(tag_name='../../bad')):
            self.assertFalse(updates.describe(release)['available'])
    def test_untrusted_asset_download_links_are_not_offered(self):
        for value in ('https://evil.example/app.zip','javascript:alert(1)','https://github.com.evil.example/file'):
            release=self.release();release['assets'][0]['browser_download_url']=value
            self.assertFalse(updates.describe(release)['available'])
    def test_cache_avoids_repeated_network_requests(self):
        with patch('clearpage.updates.urllib.request.urlopen',return_value=BytesIO(json.dumps(self.release()).encode())) as fetch:
            self.assertTrue(updates.latest_release()['available'])
            self.assertTrue(updates.latest_release()['available'])
            self.assertEqual(fetch.call_count,1)
    def test_network_errors_404_malformed_and_oversized_responses_are_safe(self):
        with patch('clearpage.updates.urllib.request.urlopen',side_effect=OSError('offline')):
            self.assertTrue(updates.latest_release()['error'])
        updates._cache.clear()
        with patch('clearpage.updates.urllib.request.urlopen',side_effect=urllib.error.HTTPError('url',404,'missing',{},None)):
            self.assertFalse(updates.latest_release()['available'])
        for raw in (b'not json',b'[]',b'x'*(1024*1024+1)):
            updates._cache.clear()
            with patch('clearpage.updates.urllib.request.urlopen',return_value=BytesIO(raw)):
                self.assertTrue(updates.latest_release()['error'])
    def test_local_api_returns_notice_without_document_or_payment_access(self):
        import app
        with patch.object(workspace,'latest_release',return_value=updates.describe(self.release())):
            response=app.app.test_client().get('/api/update-status')
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json['available'])
            self.assertEqual(app.app.test_client().get('/api/update-status',headers={'Origin':'https://other.example'}).status_code,403)
