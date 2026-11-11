"""Signed credits, manual payment boundaries, persistence and API enforcement."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from reportlab.pdfgen.canvas import Canvas
import app
import licensing as billing
from scripts.license_admin import issue, keygen


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.config = {field: 'TEST ONLY' for field in billing.FIELDS}
        self.config.update(public_key=billing.encode(self.key.public_key().public_bytes_raw()),
                           support_email='seller@example.test', swift_bic='TESTUS00XXX')
        self.config_path = self.root / 'billing.json'
        billing.atomic_json(self.config_path, self.config)
        self.store = billing.LicenseStore(self.config_path, self.root / 'profile')

    def tearDown(self):
        self.temp.cleanup()

    def token(self, identity='a' * 32, installation=None, **updates):
        data = dict(product=billing.PRODUCT, id=identity, installation=installation or self.store.installation(),
                    documents=2, amount_cents=99900, currency='INR', customer='Test customer', issued_at=int(time.time()))
        data.update(updates)
        payload = json.dumps(data).encode()
        return 'CP1.' + billing.encode(payload) + '.' + billing.encode(self.key.sign(payload))

    def file(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_activation_is_idempotent_and_credits_survive_restart(self):
        token = self.token()
        self.assertEqual(self.store.activate(token)['credits'], 2)
        self.assertEqual(self.store.activate(token)['credits'], 2)
        first = self.file('a.pdf', b'one')
        self.store.document(first, consume=True)
        self.store.document(first, consume=True)
        reopened = billing.LicenseStore(self.config_path, self.root / 'profile')
        self.assertEqual(reopened.status()['credits'], 1)
        reopened.document(self.file('renamed.pdf', b'one'), consume=True)
        reopened.document(self.file('b.pdf', b'two'), consume=True)
        self.assertEqual(reopened.status()['credits'], 0)
        with self.assertRaises(billing.LicenseRequired):
            reopened.document(self.file('d.pdf', b'four'), consume=True)
        reopened.document(first)
        self.assertEqual(reopened.activate(self.token(identity='b' * 32))['credits'], 2)

    def test_tampering_wrong_installation_and_wrong_plan_are_rejected(self):
        token = self.token()
        parts = token.split('.')
        payload = json.loads(billing.decode(parts[1]))
        payload['documents'] = 200
        tampered = '.'.join([parts[0], billing.encode(json.dumps(payload).encode()), parts[2]])
        for invalid in (tampered, self.token(installation='CP-' + 'b' * 32), self.token(documents=3),
                        self.token(amount_cents=1), self.token(currency='USD'), self.token(issued_at=int(time.time()) + 600),
                        'bad', {}, None, 'a' * 9000):
            with self.subTest(invalid=str(invalid)[:20]):
                with self.assertRaises(ValueError):
                    self.store.activate(invalid)
        self.assertEqual(self.store.status()['credits'], 0)

    def test_failed_key_does_not_replace_accepted_pack(self):
        self.store.activate(self.token())
        before = (self.store.root / 'credits.json').read_bytes()
        with self.assertRaises(ValueError):
            self.store.activate('incorrect')
        self.assertEqual((self.store.root / 'credits.json').read_bytes(), before)

    def test_legacy_packs_keep_two_credits_alongside_new_three_credit_packs(self):
        legacy = self.token(product='clearpage-two-documents-v1', documents=2,
                            amount_cents=2000, currency='USD')
        self.assertEqual(self.store.activate(legacy)['credits'], 2)
        self.assertEqual(self.store.activate(self.token(identity='c' * 32))['credits'], 4)
        self.store.document(self.file('legacy.pdf', b'old document'), consume=True)
        restored = billing.LicenseStore(self.config_path, self.root / 'profile')
        self.assertEqual(restored.status()['credits'], 3)
        self.assertEqual(restored.status()['amount_minor'], 99900)
        self.assertEqual(restored.status()['currency'], 'INR')
        self.assertEqual(restored.activate(legacy)['credits'], 3)

    def test_broken_config_and_ledger_fail_closed(self):
        self.config_path.write_text('{')
        self.assertTrue(self.store.status()['enabled'])
        self.assertIn('error', self.store.status())
        with self.assertRaises(ValueError):
            self.store.document(self.file('a.pdf', b'one'), consume=True)
        billing.atomic_json(self.config_path, self.config)
        self.store.activate(self.token())
        (self.store.root / 'credits.json').write_text('{')
        with self.assertRaises(ValueError):
            self.store.document(self.root / 'a.pdf', consume=True)

    def test_unconfigured_payments_hide_all_bank_details(self):
        self.config['support_email'] = ''
        billing.atomic_json(self.config_path, self.config)
        state = self.store.status()
        self.assertFalse(state['payment_ready'])
        self.assertNotIn('payment', state)
        self.assertNotIn('public_key', json.dumps(state))
        self.assertEqual(self.store.activate(self.token())['credits'], 2)

    def test_free_source_build_without_config(self):
        self.config_path.unlink()
        self.assertFalse(self.store.status()['enabled'])
        self.store.document(self.file('a.pdf', b'one'), consume=True)
        self.assertFalse(self.store.root.exists())

    def test_frozen_missing_changed_config_cannot_enable_free_mode(self):
        import sys
        with patch.object(sys, 'frozen', True, create=True):
            self.config_path.unlink()
            self.assertTrue(self.store.status()['enabled'])
            self.assertIn('error', self.store.status())
            with self.assertRaises(ValueError): self.store.document(self.file('unpaid.pdf', b'test'), consume=True)
            billing.atomic_json(self.config_path, self.config)
            with self.assertRaises(ValueError): self.store.document(self.root / 'unpaid.pdf', consume=True)

    def test_extra_credits_cannot_be_created_by_duplicate_saved_tokens(self):
        self.store.activate(self.token())
        billing.atomic_json(self.store.root / 'credits.json', {'tokens': [self.token(), self.token()], 'documents': []})
        self.assertIn('error', self.store.status())

    def test_owner_requires_confirmation_and_reuses_payment_reference(self):
        private, public = self.root / 'owner.pem', self.root / 'public.txt'
        password = 'test-password-only'
        keygen(private, public, password)
        self.config['public_key'] = public.read_text().strip()
        billing.atomic_json(self.config_path, self.config)
        args = (private, password, self.store.installation(), 'Customer', 'BANK-TEST-001')
        with self.assertRaises(ValueError):
            issue(*args, False)
        token = issue(*args, True)
        self.assertEqual(issue(*args, True), token)
        self.assertEqual(self.store.activate(token)['credits'], 2)
        with self.assertRaises(ValueError):
            issue(private, password, 'CP-' + 'c' * 32, 'Another customer', 'BANK-TEST-001', True)
        with self.assertRaises(ValueError):
            keygen(private, public, password)


class BillingApiTests(BillingTests):
    def setUp(self):
        super().setUp()
        self.original_config, self.original_root = app.BILLING.config_path, app.BILLING.root
        app.BILLING.config_path, app.BILLING.root = self.store.config_path, self.store.root
        self.store = app.BILLING
        app.JOBS.clear()
        app.WORK.update(active=False, zip_ready=False)
        self.client = app.app.test_client()
        self.headers = {'X-ClearPage': 'local', 'X-Filename': 'test.pdf'}

    def tearDown(self):
        import shutil
        for job in app.JOBS.values():
            shutil.rmtree(job['dir'], ignore_errors=True)
        app.JOBS.clear()
        app.BILLING.config_path, app.BILLING.root = self.original_config, self.original_root
        super().tearDown()

    def pdf(self, label):
        output = BytesIO()
        canvas = Canvas(output, pagesize=(200, 200), invariant=1)
        canvas.drawString(20, 100, label)
        canvas.save()
        return output.getvalue()

    def upload(self, content):
        return self.client.post('/api/upload', data=content, headers=self.headers)

    def test_real_upload_consumes_only_successful_distinct_files(self):
        response = self.upload(self.pdf('first'))
        self.assertEqual(response.status_code, 402, response.json)
        self.assertFalse(app.JOBS)
        response = self.client.post('/api/billing/activate', json={'key': self.token()}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.upload(b'not a PDF').status_code, 400)
        self.assertEqual(self.store.status()['credits'], 2)
        first = self.pdf('first')
        self.assertEqual(self.upload(first).status_code, 200)
        self.assertEqual(self.upload(first).status_code, 200)
        self.assertEqual(self.upload(self.pdf('second')).status_code, 200)
        self.assertEqual(self.upload(self.pdf('third')).status_code, 402)
        self.assertEqual(self.store.status()['credits'], 0)
        self.assertEqual(self.upload(first).status_code, 200)

    def test_preview_failure_does_not_spend_credit(self):
        self.store.activate(self.token())
        with patch.object(app, 'original', side_effect=ValueError('preview failed')):
            self.assertEqual(self.upload(self.pdf('first')).status_code, 400)
        self.assertEqual(self.store.status()['credits'], 2)

    def test_direct_export_download_and_queue_cannot_bypass_credits(self):
        directory = self.root / 'fake-job'
        directory.mkdir()
        source = directory / 'original.pdf'
        source.write_bytes(self.pdf('unpaid'))
        app.JOBS['unpaid'] = dict(id='unpaid', dir=directory, source=source, state='review')
        for url in ('/api/download/unpaid', '/api/page/unpaid/0/after', '/api/queue/download',
                    '/api/compress/unpaid', '/api/compressed-download/unpaid'):
            self.assertEqual(self.client.get(url).status_code, 402, url)
        for url, data in (('/api/export/unpaid', {'keep': [0]}),
                          ('/api/queue', {'ids': ['unpaid'], 'export': True}),
                          ('/api/compress/unpaid', {'source': 'original'})):
            self.assertEqual(self.client.post(url, json=data, headers=self.headers).status_code, 402, url)

    def test_accepted_document_export_still_works_without_remaining_credits(self):
        self.store.activate(self.token())
        first = self.upload(self.pdf('paid')).json['id']
        self.upload(self.pdf('second'))
        job = app.JOBS[first]
        job.update(state='review', results=[], options={'method': 'manual'})
        response = self.client.post('/api/export/' + first, json={'keep': []}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        deadline = time.monotonic() + 10
        while job['state'] == 'exporting' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(job['state'], 'done', job.get('message'))
        response = self.client.get('/api/download/' + first)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'%PDF'))
        response.close()
        self.assertEqual(self.store.status()['credits'], 0)

    def test_compression_reuses_paid_document_without_another_credit(self):
        self.store.activate(self.token())
        identifier = self.upload(self.pdf('paid compression')).json['id']
        self.upload(self.pdf('second paid document'))
        job = app.JOBS[identifier]
        for _ in range(2):
            response = self.client.post('/api/compress/'+identifier, json={}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.json)
            deadline = time.monotonic()+10
            while job['state'] == 'exporting' and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(job['compression']['status'], 'ready', job['compression'])
            with self.client.get('/api/compressed-download/'+identifier) as download:
                self.assertEqual(download.status_code, 200)
            self.assertEqual(self.store.status()['credits'], 0)

    def test_activation_requires_local_request_and_cannot_confirm_payment(self):
        for path in ('/api/billing/razorpay/order', '/api/billing/razorpay/check'):
            self.assertEqual(self.client.post(path, json={}).status_code, 403)
            self.assertEqual(self.client.post(path, json={}, headers={**self.headers,
                'Origin': 'https://elsewhere.example'}).status_code, 403)
            self.assertEqual(self.client.post(path, json={}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/billing/activate', json={'key': self.token()}).status_code, 403)
        self.assertEqual(self.client.post('/api/billing/activate', json={'key': self.token()},
                         headers={**self.headers, 'Origin': 'https://elsewhere.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/billing/activate', json={'paid': True, 'reference': '123'},
                                         headers=self.headers).status_code, 400)
        self.assertEqual(self.store.status()['credits'], 0)


if __name__ == '__main__':
    unittest.main()
