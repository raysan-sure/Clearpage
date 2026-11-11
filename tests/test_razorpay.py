"""Local HTTP simulation of Razorpay; never contacts or charges a real account."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from flask import Flask, jsonify, request
from werkzeug.serving import make_server, WSGIRequestHandler
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from clearpage.billing.licensing import LicenseStore, atomic_json, encode, verify, LicenseRequired
from payment_service.server import create_app
from clearpage.billing.payment_client import PaymentClient, service_url
from clearpage.billing.payment_http import PaymentUnavailable, json_request


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


@contextmanager
def running(app):
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


class RazorpayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.public = encode(self.key.public_key().public_bytes_raw())
        self.private = self.root / 'private.pem'
        self.private.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b'testing-password')))
        self.settings = dict(RAZORPAY_KEY_ID='rzp_test_fake', RAZORPAY_KEY_SECRET='not-a-real-secret',
            RAZORPAY_WEBHOOK_SECRET='not-a-real-webhook-secret', CLEARPAGE_SIGNING_KEY=str(self.private),
            CLEARPAGE_SIGNING_PASSWORD='testing-password', CLEARPAGE_PAYMENT_DB=str(self.root / 'db.sqlite3'))
        self.service = create_app(self.settings)
        self.client = self.service.test_client()
        self.claim = 'a' * 64
        self.account_secret = 'f' * 64
        self.installation = 'CP-' + hashlib.sha256(self.account_secret.encode()).hexdigest()[:32]
        self.orders = []
        self.payments = {}
        self.fail = False
        self.calls = []
        mock = Flask('razorpay-contract-simulation')

        @mock.before_request
        def observe():
            self.calls.append((request.method, request.path))
            if self.fail:
                return '<html>upstream unavailable</html>', 503
            if not request.headers.get('Authorization', '').startswith('Basic '):
                return jsonify(error='authentication required'), 401

        @mock.post('/v1/orders')
        def order():
            value = request.get_json()
            self.orders.append(value)
            return jsonify(id=f'order_{len(self.orders)}', **value)

        @mock.get('/v1/orders/<identifier>/payments')
        def payments(identifier):
            return jsonify(items=[p for p in self.payments.values() if p['order_id'] == identifier])

        @mock.get('/v1/payments/<identifier>')
        def payment(identifier):
            return jsonify(self.payments[identifier]) if identifier in self.payments else (jsonify(error='missing'), 404)

        self.mock_running = running(mock)
        self.mock_url = self.mock_running.__enter__()
        self.service.extensions['razorpay'].base = self.mock_url + '/v1'

    def tearDown(self):
        self.mock_running.__exit__(None, None, None)
        self.temp.cleanup()

    def create(self, **updates):
        value = dict(claim=self.claim, installation=self.installation, public_key=self.public, account_secret=self.account_secret)
        value.update(updates)
        return self.client.post('/api/orders', json=value)

    def captured(self, order='order_1', **updates):
        value = dict(id='pay_1', order_id=order, amount=9900, currency='USD', status='captured',
                     captured=True, amount_refunded=0)
        value.update(updates)
        self.payments[value['id']] = value
        return value

    def confirm(self, **updates):
        value = dict(claim=self.claim, razorpay_order_id='order_1', razorpay_payment_id='pay_1',
                     razorpay_signature=hmac.new(b'not-a-real-secret', b'order_1|pay_1', hashlib.sha256).hexdigest())
        value.update(updates)
        return self.client.post('/api/confirm', json=value)

    def test_fixed_price_idempotent_orders_and_signed_fulfillment(self):
        first = self.create(amount=1, documents=200, currency='INR')
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json['amount'], 9900)
        self.assertEqual(first.json['currency'], 'USD')
        self.assertNotIn('license', first.json)
        self.assertEqual(self.create().json['order_id'], first.json['order_id'])
        self.assertEqual(len(self.orders), 1)
        self.captured()
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.json)
        token = response.json['license']
        data = verify(token, self.public, self.installation)
        self.assertEqual(data['documents'], 2)
        self.assertEqual(self.confirm().json['license'], token)
        self.assertEqual(self.client.post('/api/purchase', json={'claim': self.claim}).json['license'], token)
        self.assertIn(('POST', '/v1/orders'), self.calls)
        self.assertIn(('GET', '/v1/payments/pay_1'), self.calls)

    def test_wrong_amount_currency_or_order_rejected(self):
        self.create()
        for update in ({'amount': 100}, {'currency': 'INR'}, {'order_id': 'order_wrong'}):
            with self.subTest(update=update):
                self.captured(**update)
                response = self.confirm()
                self.assertEqual(response.status_code, 400, response.json)
                self.assertNotIn('license', response.json)

    def test_failed_authorized_and_refunded_payments_never_grant(self):
        self.create()
        for update in ({'status': 'failed', 'captured': False}, {'status': 'authorized', 'captured': False},
                       {'amount_refunded': 1}, {'status': 'refunded'}):
            with self.subTest(update=update):
                self.captured(**update)
                self.assertNotIn('license', self.confirm().json)

    def test_signatures_and_other_installation_rejected(self):
        self.create()
        self.captured()
        for update in ({'razorpay_signature': 'fake'}, {'razorpay_order_id': 'order_2'},
                       {'claim': 'c' * 64}, {'razorpay_payment_id': '../../orders'},
                       {'razorpay_signature': None}):
            self.assertEqual(self.confirm(**update).status_code, 400)
        self.assertEqual(self.create(installation='CP-' + 'd' * 32).status_code, 400)
        self.assertEqual(self.create(public_key='wrong').status_code, 400)
        self.assertEqual(len(self.orders), 1)

    def test_webhook_raw_signature_replay_and_recovery_after_restart(self):
        self.create()
        payment = self.captured()
        body = json.dumps({'event': 'payment.captured', 'payload': {'payment': {'entity': payment}}}).encode()
        signature = hmac.new(b'not-a-real-webhook-secret', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.client.post('/api/webhook', data=body, content_type='application/json').status_code, 400)
        headers = {'X-Razorpay-Signature': signature}
        for _ in range(2):
            self.assertEqual(self.client.post('/api/webhook', data=body, content_type='application/json', headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/webhook', data=body+b' ', content_type='application/json', headers=headers).status_code, 400)
        restored = create_app(self.settings)
        restored.extensions['razorpay'].base = self.mock_url + '/v1'
        response = restored.test_client().post('/api/purchase', json={'claim': self.claim})
        self.assertEqual(verify(response.json['license'], self.public, self.installation)['documents'], 2)

    def test_network_failure_never_grants_and_recovers_same_order(self):
        self.create()
        self.captured()
        self.fail = True
        response = self.confirm()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('license', response.json)
        self.assertNotIn('not-a-real-secret', response.get_data(as_text=True))
        self.fail = False
        self.assertEqual(self.create().json['status'], 'paid')
        self.assertEqual(len(self.orders), 1)

    def test_concurrent_retries_create_one_order(self):
        def create_one(_):
            return self.service.test_client().post('/api/orders', json=dict(claim=self.claim,
                installation=self.installation, public_key=self.public, account_secret=self.account_secret)).json['order_id']
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(create_one, range(4)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(self.orders), 1)

    def test_test_and_live_keys_cannot_share_database(self):
        with self.assertRaises(ValueError):
            create_app({**self.settings, 'RAZORPAY_KEY_ID': 'rzp_live_fake'})

    def test_database_upgrade_preserves_legacy_orders(self):
        database = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute('CREATE TABLE purchases (claim_hash TEXT PRIMARY KEY, installation TEXT NOT NULL, order_id TEXT UNIQUE NOT NULL, created INTEGER NOT NULL, license TEXT, payment_id TEXT UNIQUE)')
            connection.execute('INSERT INTO purchases VALUES (?, ?, ?, ?, NULL, NULL)',
                (hashlib.sha256(self.claim.encode()).hexdigest(), self.installation, 'order_legacy', 1))
        upgraded = create_app({**self.settings, 'CLEARPAGE_PAYMENT_DB': str(database)})
        upgraded.extensions['razorpay'].base = self.mock_url + '/v1'
        self.captured(order='order_legacy', amount=2000, currency='USD')
        response = upgraded.test_client().post('/api/purchase', json={'claim': self.claim})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['amount'], 2000)
        self.assertEqual(response.json['currency'], 'USD')
        self.assertEqual(response.json['documents'], 2)
        self.assertEqual(verify(response.json['license'], self.public, self.installation)['documents'], 2)
        fresh = upgraded.test_client().post('/api/orders', json=dict(claim='e' * 64,
            installation=self.installation, public_key=self.public, account_secret=self.account_secret))
        self.assertEqual(fresh.status_code, 200, fresh.json)
        self.assertEqual((fresh.json['amount'], fresh.json['currency'], fresh.json['documents']), (9900, 'USD', 2))

    def test_refunded_purchase_does_not_block_a_new_pack(self):
        with running(self.service) as url, patch.dict('os.environ', {'CLEARPAGE_PAYMENT_LOCAL_TEST': '1'}):
            config = self.root / 'billing.json'
            atomic_json(config, dict(payment_provider='razorpay', payment_service_url=url,
                seller_name='Test seller', support_email='seller@example.test', public_key=self.public))
            store = LicenseStore(config, self.root / 'desktop')
            desktop = PaymentClient(store)
            desktop.checkout(create=True)
            self.captured(status='refunded', amount_refunded=9900)
            response = desktop.checkout()
            self.assertTrue(response['refunded'])
            self.assertFalse(response['pending'])
            self.assertEqual(store.status()['credits'], 0)
            self.assertTrue(desktop.checkout(create=True)['pending'])
            self.assertEqual(len(self.orders), 2)
            self.captured(order='order_2', id='pay_2')
            self.assertEqual(desktop.checkout()['billing']['credits'], 2)

    def test_rate_limit_and_bad_request(self):
        self.assertEqual(self.client.post('/api/orders', json=[]).status_code, 400)
        self.assertEqual(self.create(claim='short').status_code, 400)
        for _ in range(11):
            response = self.create()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(self.orders), 1)

    def test_desktop_real_http_recovery_and_exact_document_credits(self):
        with running(self.service) as url, patch.dict('os.environ', {'CLEARPAGE_PAYMENT_LOCAL_TEST': '1'}):
            config = self.root / 'billing.json'
            atomic_json(config, dict(payment_provider='razorpay', payment_service_url=url,
                seller_name='Test seller', support_email='seller@example.test', public_key=self.public))
            store = LicenseStore(config, self.root / 'desktop')
            desktop = PaymentClient(store)
            result = desktop.checkout(create=True)
            self.assertTrue(result['pending'])
            self.assertEqual(store.status()['credits'], 0)
            self.assertNotIn('claim', store.status())
            self.captured()
            # Restart client and complete via GET-payment recovery, without browser callback.
            fresh = PaymentClient(LicenseStore(config, self.root / 'desktop'))
            result = fresh.checkout()
            self.assertTrue(result['paid'])
            self.assertEqual(result['billing']['credits'], 2)
            self.assertEqual(fresh.checkout()['billing']['credits'], 2)
            first = self.root / 'one.pdf'; first.write_bytes(b'first document')
            second = self.root / 'two.pdf'; second.write_bytes(b'second document')
            third = self.root / 'three.pdf'; third.write_bytes(b'third document')
            fourth = self.root / 'four.pdf'; fourth.write_bytes(b'fourth document')
            for file in (first, first, second, first):
                store.document(file, consume=True)
            with self.assertRaises(LicenseRequired):
                store.document(fourth, consume=True)
            self.assertEqual(store.status()['credits'], 0)
            # A new genuine purchase creates one more order and adds exactly three credits.
            desktop.checkout(create=True)
            self.captured(order='order_2', id='pay_2')
            self.assertEqual(desktop.checkout()['billing']['credits'], 2)

    def test_desktop_retains_pending_purchase_during_outage(self):
        with running(self.service) as url, patch.dict('os.environ', {'CLEARPAGE_PAYMENT_LOCAL_TEST': '1'}):
            config = self.root / 'billing.json'
            atomic_json(config, dict(payment_provider='razorpay', payment_service_url=url,
                seller_name='Test seller', support_email='seller@example.test', public_key=self.public))
            store = LicenseStore(config, self.root / 'desktop')
            desktop = PaymentClient(store)
            desktop.checkout(create=True)
            saved = (store.root / 'razorpay-purchase.json').read_bytes()
            self.fail = True
            with self.assertRaises(PaymentUnavailable):
                desktop.checkout()
            self.assertEqual((store.root / 'razorpay-purchase.json').read_bytes(), saved)
            self.assertEqual(store.status()['credits'], 0)
            self.fail = False
            self.captured()
            self.assertTrue(desktop.checkout()['paid'])

    def test_https_required_and_service_secrets_never_returned(self):
        for url in ('http://example.com', 'https://user:password@example.com', 'https://example.com/path', 'https://example.com?q=secret'):
            with self.assertRaises(ValueError):
                service_url({'payment_service_url': url})
        response = self.create()
        text = response.get_data(as_text=True)
        self.assertNotIn(self.settings['RAZORPAY_KEY_SECRET'], text)
        self.assertNotIn(self.settings['RAZORPAY_WEBHOOK_SECRET'], text)
        self.assertNotIn('BEGIN ENCRYPTED PRIVATE KEY', text)
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_online_ledger_rollback_and_identity_reset_do_not_restore_credits(self):
        with running(self.service) as url, patch.dict('os.environ', {'CLEARPAGE_PAYMENT_LOCAL_TEST': '1'}):
            config = self.root / 'billing.json'
            atomic_json(config, dict(payment_provider='razorpay', payment_service_url=url,
                seller_name='Test seller', support_email='seller@example.test', public_key=self.public))
            store = LicenseStore(config, self.root / 'desktop')
            buyer = PaymentClient(store)
            buyer.checkout(create=True); self.captured(); buyer.checkout()
            first, second, third = [self.root / name for name in ('first.pdf', 'second.pdf', 'third.pdf')]
            for n, path in enumerate((first, second, third)): path.write_bytes(str(n).encode())
            store.document(first, consume=True); store.document(second, consume=True)
            atomic_json(store.root / 'credits.json', {'tokens': [], 'documents': []})
            restarted = LicenseStore(config, self.root / 'desktop')
            self.assertEqual(restarted.status()['credits'], 0)
            with self.assertRaises(LicenseRequired): restarted.document(third, consume=True)
            restarted.document(first, consume=True)
            saved_identity = (store.root / 'online-account.json').read_bytes()
            (store.root / 'online-account.json').unlink()
            self.assertEqual(restarted.status()['credits'], 0)
            with self.assertRaises(LicenseRequired): restarted.document(first, consume=True)
            (store.root / 'online-account.json').write_bytes(saved_identity)
            restarted.document(first)
            with self.assertRaises(ValueError): restarted.activate('forged-offline-pack')

    def test_concurrent_online_document_spending_is_atomic(self):
        self.create(); self.captured(); self.confirm()
        def spend(index):
            response = self.service.test_client().post('/api/credits', json=dict(
                installation=self.installation, account_secret=self.account_secret,
                nonce=f'{index:032x}', digest=f'{index:064x}', consume=True))
            self.assertEqual(response.status_code, 200, response.json)
            from clearpage.billing.licensing import decode
            return json.loads(decode(response.json['payload']))['authorized']
        with ThreadPoolExecutor(max_workers=6) as pool:
            allowed = list(pool.map(spend, range(1, 7)))
        self.assertEqual(sum(allowed), 2)

    def test_credit_authentication_and_signed_reply_replay(self):
        value = dict(installation=self.installation, account_secret='e' * 64, nonce='b'*32)
        self.assertEqual(self.client.post('/api/credits', json=value).status_code, 400)
        with running(self.service) as url, patch.dict('os.environ', {'CLEARPAGE_PAYMENT_LOCAL_TEST': '1'}):
            config = self.root / 'billing.json'
            atomic_json(config, dict(payment_provider='razorpay', payment_service_url=url,
                seller_name='Test seller', support_email='seller@example.test', public_key=self.public))
            store = LicenseStore(config, self.root / 'desktop')
            from clearpage.billing.online_credits import identity, query
            response = self.client.post('/api/credits', json={**identity(store.root), 'nonce':'c'*32}).json
            with patch('clearpage.billing.online_credits.json_request', return_value=response):
                with self.assertRaises(ValueError): query(store, json.loads(config.read_text()))
            response['signature'] = 'AA'
            with patch('clearpage.billing.online_credits.json_request', return_value=response):
                with self.assertRaises(ValueError): query(store, json.loads(config.read_text()))

    def test_refund_revokes_online_permission(self):
        self.create(); self.captured(); self.confirm()
        body = dict(installation=self.installation, account_secret=self.account_secret,
                    nonce='a'*32, digest='a'*64, consume=True)
        from clearpage.billing.licensing import decode
        self.assertTrue(json.loads(decode(self.client.post('/api/credits', json=body).json['payload']))['authorized'])
        self.captured(status='refunded', amount_refunded=9900)
        payment = self.payments['pay_1']
        raw = json.dumps({'event':'payment.refunded','payload':{'payment':{'entity':payment}}}).encode()
        signature = hmac.new(b'not-a-real-webhook-secret', raw, hashlib.sha256).hexdigest()
        response = self.client.post('/api/webhook', data=raw, content_type='application/json',headers={'X-Razorpay-Signature':signature})
        self.assertEqual(response.status_code, 200)
        balance = json.loads(decode(self.client.post('/api/credits',json=body).json['payload']))
        self.assertFalse(balance['authorized']); self.assertEqual(balance['credits'], 0)


if __name__ == '__main__':
    unittest.main()
