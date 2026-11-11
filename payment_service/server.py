"""Razorpay orders, capture verification and signed document-pack fulfillment."""
import base64
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from flask import Flask, jsonify, request, send_from_directory
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from clearpage.billing.licensing import DOCUMENTS, PRICE_MINOR, CURRENCY, PRODUCT, PLANS, encode
from clearpage.billing.payment_http import json_request, PaymentUnavailable


class Razorpay:
    def __init__(self, key_id, secret, base='https://api.razorpay.com/v1'):
        self.key_id, self.secret, self.base = key_id, secret, base

    def call(self, path, body=None):
        auth = base64.b64encode(f'{self.key_id}:{self.secret}'.encode()).decode()
        return json_request(self.base + path, body, {'Authorization': 'Basic ' + auth})


def create_app(settings=None):
    settings = dict(os.environ) if settings is None else settings
    required = ('RAZORPAY_KEY_ID', 'RAZORPAY_KEY_SECRET', 'RAZORPAY_WEBHOOK_SECRET',
                'CLEARPAGE_SIGNING_KEY', 'CLEARPAGE_SIGNING_PASSWORD', 'CLEARPAGE_PAYMENT_DB')
    if any(not settings.get(k) for k in required):
        raise ValueError('Configure all seller payment environment variables. See docs/RAZORPAY.md.')
    key_id = settings['RAZORPAY_KEY_ID']
    if not re.fullmatch(r'rzp_(test|live)_[A-Za-z0-9]+', key_id):
        raise ValueError('Invalid Razorpay key ID.')
    key = serialization.load_pem_private_key(Path(settings['CLEARPAGE_SIGNING_KEY']).read_bytes(),
                                           password=settings['CLEARPAGE_SIGNING_PASSWORD'].encode())
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Use an Ed25519 ClearPage signing key.')
    public_key = encode(key.public_key().public_bytes_raw())
    provider = Razorpay(key_id, settings['RAZORPAY_KEY_SECRET'])
    database = Path(settings['CLEARPAGE_PAYMENT_DB']).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 65536
    app.extensions['razorpay'] = provider
    assets = Path(__file__).parent / 'web'

    def db():
        connection = sqlite3.connect(database, timeout=25)
        connection.row_factory = sqlite3.Row
        return connection

    with closing(db()) as connection, connection:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS purchases (
                claim_hash TEXT PRIMARY KEY, installation TEXT NOT NULL,
                order_id TEXT UNIQUE NOT NULL, created INTEGER NOT NULL,
                license TEXT, payment_id TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL);
        ''')
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(purchases)')}
        if 'product' not in columns:
            # Existing orders retain their USD/two-document plan after the upgrade.
            connection.execute("ALTER TABLE purchases ADD COLUMN product TEXT NOT NULL DEFAULT 'clearpage-two-documents-v1'")
        if 'revoked' not in columns:
            connection.execute('ALTER TABLE purchases ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0')
        connection.execute('CREATE TABLE IF NOT EXISTS document_access (installation TEXT NOT NULL, digest TEXT NOT NULL, order_id TEXT NOT NULL REFERENCES purchases(order_id), PRIMARY KEY(installation, digest))')
        # Prevent test/live key or signing-key swaps from silently corrupting a ledger.
        identity = key_id + ':' + public_key
        previous = connection.execute("SELECT value FROM metadata WHERE name='identity'").fetchone()
        if previous and previous['value'] != identity:
            raise ValueError('This database belongs to different payment/signing keys. Use a separate test/live database.')
        connection.execute("INSERT OR IGNORE INTO metadata VALUES ('identity', ?)", (identity,))

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self' https://checkout.razorpay.com; "
            "style-src 'self' 'unsafe-inline'; frame-src https://*.razorpay.com; "
            "connect-src 'self' https://api.github.com https://*.razorpay.com; img-src 'self' data: https://*.razorpay.com; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(PaymentUnavailable)
    def unavailable(error):
        return jsonify(error=str(error)), 503

    @app.errorhandler(Exception)
    def unexpected(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        # Payment responses and credentials must never appear in client errors.
        return jsonify(error='Payment service unavailable. Check payment before paying again.'), 503

    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object.')
        return value

    def claim_hash(value):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
            raise ValueError('Invalid purchase access code.')
        return hashlib.sha256(value.encode()).hexdigest()

    def account(value):
        secret = value.get('account_secret')
        if not isinstance(secret, str) or not re.fullmatch('[a-f0-9]{64}', secret):
            raise ValueError('Invalid credit-account credentials.')
        installation = 'CP-' + hashlib.sha256(secret.encode()).hexdigest()[:32]
        if value.get('installation') != installation:
            raise ValueError('Credit-account identity does not match its credentials.')
        return installation

    def balance(connection, installation):
        grants = connection.execute('SELECT order_id, product FROM purchases WHERE installation=? AND license IS NOT NULL AND revoked=0', (installation,)).fetchall()
        used = dict(connection.execute('SELECT order_id, count(*) FROM document_access WHERE installation=? GROUP BY order_id', (installation,)).fetchall())
        purchased = sum(PLANS[g['product']][2] for g in grants)
        spent = sum(used.get(g['order_id'], 0) for g in grants)
        return grants, used, dict(credits=max(0, purchased-spent), purchased=purchased, used=spent)

    @app.post('/api/credits')
    def credits():
        rate_limit('credits', 180)
        value = data()
        installation = account(value)
        nonce, digest = value.get('nonce'), value.get('digest')
        if not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce):
            raise ValueError('Invalid credit request nonce.')
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Invalid document fingerprint.')
        if type(value.get('consume', False)) is not bool:
            raise ValueError('Invalid document operation.')
        authorized = False
        admin_expiry = admin_for(value, installation)
        with closing(db()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            grants, used, result = balance(connection, installation)
            active = {g['order_id'] for g in grants}
            if digest is not None and admin_expiry:
                authorized = True
            elif digest is not None:
                existing = connection.execute('SELECT order_id FROM document_access WHERE installation=? AND digest=?', (installation, digest)).fetchone()
                authorized = bool(existing and existing['order_id'] in active)
                if not authorized and not existing and value.get('consume'):
                    available = next((g for g in grants if used.get(g['order_id'], 0) < PLANS[g['product']][2]), None)
                    if available:
                        connection.execute('INSERT INTO document_access VALUES (?, ?, ?)', (installation, digest, available['order_id']))
                        authorized = True
                result = balance(connection, installation)[2]
        payload = dict(**result, installation=installation, nonce=nonce, digest=digest,
                       authorized=authorized, issued_at=int(time.time()), admin=bool(admin_expiry),
                       admin_expires_at=admin_expiry or 0)
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        return jsonify(payload=encode(raw), signature=encode(key.sign(raw)))

    def rate_limit(scope, maximum):
        # SQLite works across workers. Do not trust client-supplied forwarding headers.
        bucket = f'{scope}:{hashlib.sha256((request.remote_addr or "unknown").encode()).hexdigest()}:{int(time.time()) // 60}'
        with closing(db()) as connection, connection:
            connection.execute('INSERT INTO limits VALUES (?, 1) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (bucket,))
            count = connection.execute('SELECT count FROM limits WHERE bucket=?', (bucket,)).fetchone()[0]
            connection.execute('DELETE FROM limits WHERE CAST(substr(bucket, -8) AS INTEGER) < ?', (int(time.time()) // 60 - 2,))
        if count > maximum:
            from werkzeug.exceptions import TooManyRequests
            raise TooManyRequests('Too many payment requests. Wait a minute and retry.')

    from payment_service.admin import register_admin
    admin_for = register_admin(app, settings, db, data, account, rate_limit)

    def purchase(digest):
        with closing(db()) as connection:
            row = connection.execute('SELECT * FROM purchases WHERE claim_hash=?', (digest,)).fetchone()
        if not row:
            raise ValueError('Purchase not found. Return to ClearPage and start checkout.')
        return row

    def describe(row):
        amount, currency, documents = PLANS[row['product']]
        return dict(order_id=row['order_id'], amount=amount, currency=currency, documents=documents,
                    key_id=key_id, test_mode=key_id.startswith('rzp_test_'),
                    seller_name=settings.get('CLEARPAGE_SELLER_NAME', 'ClearPage'),
                    support_email=settings.get('CLEARPAGE_SUPPORT_EMAIL', ''), status='pending')

    def verify_payment(row, payment):
        amount, currency, documents = PLANS[row['product']]
        if (payment.get('order_id') != row['order_id'] or payment.get('amount') != amount
                or payment.get('currency') != currency
                or not re.fullmatch(r'pay_[A-Za-z0-9]+', str(payment.get('id', '')))):
            raise ValueError('Payment does not match the amount and currency of this order.')
        if payment.get('amount_refunded', 0) or payment.get('status') == 'refunded':
            with closing(db()) as connection, connection:
                connection.execute('UPDATE purchases SET revoked=1 WHERE order_id=?', (row['order_id'],))
            return {**describe(row), 'status': 'refunded'}
        if payment.get('status') != 'captured' or payment.get('captured') is not True:
            return describe(row)
        payload = dict(product=row['product'], id=hashlib.sha256(('razorpay:' + row['order_id']).encode()).hexdigest()[:32],
                       installation=row['installation'], customer=payment['id'], documents=documents,
                       amount_cents=amount, currency=currency, issued_at=int(time.time()))
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        token = 'CP1.' + encode(raw) + '.' + encode(key.sign(raw))
        with closing(db()) as connection, connection:
            connection.execute('UPDATE purchases SET license=?, payment_id=? WHERE claim_hash=? AND license IS NULL',
                               (token, payment['id'], row['claim_hash']))
            token = connection.execute('SELECT license FROM purchases WHERE claim_hash=?', (row['claim_hash'],)).fetchone()[0]
        return {**describe(row), 'status': 'paid', 'license': token}

    def refresh(row):
        if row['payment_id']:
            return verify_payment(row, provider.call('/payments/' + row['payment_id']))
        payments = provider.call('/orders/' + row['order_id'] + '/payments')
        items = payments.get('items')
        if not isinstance(items, list):
            raise PaymentUnavailable('Unable to check payment. Try again before paying again.')
        for payment in items:
            if payment.get('status') in ('captured', 'refunded'):
                return verify_payment(row, payment)
        return describe(row)

    @app.get('/health')
    def health():
        return jsonify(status='ok', test_mode=key_id.startswith('rzp_test_'))

    @app.get('/checkout')
    def checkout():
        return send_from_directory(assets, 'checkout.html')

    @app.get('/checkout.js')
    def checkout_js():
        return send_from_directory(assets, 'checkout.js')

    @app.get('/checkout.css')
    def checkout_css():
        return send_from_directory(assets, 'checkout.css')

    @app.post('/api/orders')
    def orders():
        rate_limit('create', 12)
        value = data()
        account(value)
        digest = claim_hash(value.get('claim'))
        installation = value.get('installation')
        if not isinstance(installation, str) or not re.fullmatch(r'CP-[a-f0-9]{32}', installation):
            raise ValueError('Invalid installation code.')
        if value.get('public_key') != public_key:
            raise ValueError('Payment service signing key does not match this app. Contact the seller.')
        with closing(db()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM purchases WHERE claim_hash=?', (digest,)).fetchone()
            if row and row['installation'] != installation:
                raise ValueError('Purchase belongs to another installation.')
            if not row:
                order = provider.call('/orders', dict(amount=PRICE_MINOR, currency=CURRENCY,
                    receipt='cp_' + digest[:36], partial_payment=False,
                    notes={'product': PRODUCT, 'installation': installation}))
                if (order.get('amount') != PRICE_MINOR or order.get('currency') != CURRENCY
                        or not re.fullmatch(r'order_[A-Za-z0-9]+', str(order.get('id', '')))):
                    raise PaymentUnavailable('Unexpected payment order. Contact the seller before retrying.')
                connection.execute('INSERT INTO purchases(claim_hash, installation, order_id, created, product) VALUES (?, ?, ?, ?, ?)',
                                   (digest, installation, order['id'], int(time.time()), PRODUCT))
                row = connection.execute('SELECT * FROM purchases WHERE claim_hash=?', (digest,)).fetchone()
        return jsonify(refresh(row))

    @app.post('/api/purchase')
    def status():
        rate_limit('check', 120)
        row = purchase(claim_hash(data().get('claim')))
        return jsonify(refresh(row))

    @app.post('/api/confirm')
    def confirm():
        rate_limit('check', 120)
        value = data()
        row = purchase(claim_hash(value.get('claim')))
        payment_id = value.get('razorpay_payment_id', '')
        if not isinstance(payment_id, str) or not re.fullmatch(r'pay_[A-Za-z0-9]+', payment_id):
            raise ValueError('Invalid payment ID.')
        expected = hmac.new(provider.secret.encode(), (row['order_id'] + '|' + payment_id).encode(), hashlib.sha256).hexdigest()
        signature = value.get('razorpay_signature')
        if (value.get('razorpay_order_id') != row['order_id'] or not isinstance(signature, str)
                or not hmac.compare_digest(expected, signature)):
            raise ValueError('Payment signature verification failed.')
        return jsonify(verify_payment(row, provider.call('/payments/' + payment_id)))

    @app.post('/api/webhook')
    def webhook():
        raw = request.get_data()
        expected = hmac.new(settings['RAZORPAY_WEBHOOK_SECRET'].encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get('X-Razorpay-Signature', '')):
            return jsonify(error='Invalid webhook signature.'), 400
        value = data()
        if value.get('event') not in ('payment.captured', 'order.paid', 'payment.refunded', 'refund.processed'):
            return jsonify(received=True)
        payment = value.get('payload', {}).get('payment', {}).get('entity', {})
        if value.get('event') == 'refund.processed':
            refund = value.get('payload', {}).get('refund', {}).get('entity', {})
            payment = provider.call('/payments/' + refund.get('payment_id', ''))
        order_id, payment_id = payment.get('order_id'), payment.get('id')
        if not isinstance(payment_id, str) or not re.fullmatch(r'pay_[A-Za-z0-9]+', payment_id):
            raise ValueError('Invalid webhook payment.')
        with closing(db()) as connection:
            row = connection.execute('SELECT * FROM purchases WHERE order_id=?', (order_id,)).fetchone()
        if row:
            verify_payment(row, provider.call('/payments/' + payment_id))
        return jsonify(received=True)

    return app


if __name__ == '__main__':
    # Production: use a WSGI server behind HTTPS; never Flask's development server.
    create_app().run(host='127.0.0.1', port=5056)
