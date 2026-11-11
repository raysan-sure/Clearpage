"""Desktop client: public service URL and signed licenses only; no merchant secrets."""
import json
import os
import secrets
from urllib.parse import urlsplit, urlencode
from flask import jsonify
from clearpage.billing.licensing import configuration, payment_ready, atomic_json
from clearpage.billing.online_credits import identity
from clearpage.billing.payment_http import json_request


def service_url(config):
    value = config.get('payment_service_url', '').rstrip('/')
    parsed = urlsplit(value)
    local_test = (os.environ.get('CLEARPAGE_PAYMENT_LOCAL_TEST') == '1'
                  and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'))
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path or (parsed.scheme != 'https' and not local_test)):
        raise ValueError('Configure an HTTPS payment service URL without a path.')
    return value


class PaymentClient:
    def __init__(self, store):
        self.store = store

    def config(self):
        config = self.store.config()
        if not config or config.get('payment_provider') != 'razorpay' or not payment_ready(config):
            raise ValueError('Razorpay is not configured yet. Contact the seller.')
        return config

    def pending(self, url):
        path = self.store.root / 'razorpay-purchase.json'
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or value.get('service') != url
                    or not isinstance(value.get('claim'), str) or len(value['claim']) != 64):
                raise ValueError()
            return value
        except (ValueError, OSError) as error:
            raise ValueError('Saved payment cannot be recovered. Contact the seller before paying again.') from error

    def checkout(self, create=False):
        with self.store.lock:
            config = self.config()
            url = service_url(config)
            saved = self.pending(url)
            if saved is None:
                if not create:
                    return {'billing': self.store.status(), 'pending': False}
                # Save capability BEFORE the network call. A lost response can safely retry.
                saved = {'service': url, 'claim': secrets.token_hex(32)}
                atomic_json(self.store.root / 'razorpay-purchase.json', saved)
            claim = saved['claim']
            if create:
                response = json_request(url + '/api/orders', {
                    'claim': claim, 'installation': self.store.installation(),
                    'account_secret': identity(self.store.root)['account_secret'],
                    'public_key': config['public_key']})
            else:
                response = json_request(url + '/api/purchase', {'claim': claim})
            if response.get('status') == 'paid' and isinstance(response.get('license'), str):
                billing = self.store.status()
                if billing.get('error'):
                    raise ValueError('Payment received. Keep this profile and use Check payment when the credit service is available.')
                (self.store.root / 'razorpay-purchase.json').unlink(missing_ok=True)
                return {'billing': billing, 'pending': False, 'paid': True}
            if response.get('status') == 'refunded':
                # A confirmed refund must not trap all future purchases on this order.
                (self.store.root / 'razorpay-purchase.json').unlink(missing_ok=True)
                return {'billing': self.store.status(), 'pending': False, 'refunded': True}
            return {'billing': self.store.status(), 'pending': True,
                    'checkout_url': url + '/checkout#' + urlencode({'claim': claim}),
                    'amount': response.get('amount'), 'currency': response.get('currency'),
                    'documents': response.get('documents'),
                    'test_mode': response.get('test_mode', False)}


def register_payments(app, store):
    client = PaymentClient(store)

    @app.post('/api/billing/razorpay/order')
    def create_order():
        return jsonify(client.checkout(create=True))

    @app.post('/api/billing/razorpay/check')
    def check_order():
        return jsonify(client.checkout())
