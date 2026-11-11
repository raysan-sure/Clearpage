"""Signed document packs with manual or seller-hosted Razorpay fulfillment."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import sys
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT = 'clearpage-two-documents-usd-99-v1'
PRICE_MINOR = 9900
CURRENCY = 'USD'
DOCUMENTS = 2
# Keep previously signed packs valid at their original value. The historical
# amount_cents token field holds currency subunits (paise for INR).
PLANS = {PRODUCT: (PRICE_MINOR, CURRENCY, DOCUMENTS),
         'clearpage-two-documents-inr-v1': (99900, 'INR', 2),
         'clearpage-three-documents-inr-v1': (99900, 'INR', 3),
         'clearpage-two-documents-v1': (2000, 'USD', 2)}
FIELDS = ('seller_name', 'support_email', 'beneficiary_name', 'account_number',
          'bank_name', 'bank_address', 'swift_bic', 'transfer_instructions', 'public_key')
GATEWAY_FIELDS = ('payment_provider', 'payment_service_url')
FROZEN_CONFIG_HASH = None  # Set by the compiled customer-build runtime hook.


class LicenseRequired(ValueError):
    pass


def encode(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid activation key.')
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def configuration(path):
    """An absent config preserves the existing free source build."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 32768:
            raise ValueError()
        config = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict) or set(config) - set(FIELDS + GATEWAY_FIELDS):
            raise ValueError()
        if any(not isinstance(config.get(k, ''), str) or len(config.get(k, '')) > 4000 for k in FIELDS + GATEWAY_FIELDS):
            raise ValueError()
        result = {k: config.get(k, '').strip() for k in FIELDS + GATEWAY_FIELDS}
        if result['payment_provider'] not in ('', 'manual', 'razorpay'):
            raise ValueError()
        return result
    except (ValueError, OSError) as error:
        raise ValueError('Payment configuration is invalid. Contact the seller.') from error


def payment_ready(config):
    if config and config.get('payment_provider') == 'razorpay':
        from clearpage.billing.payment_client import service_url
        try:
            service_url(config)
            Ed25519PublicKey.from_public_bytes(decode(config['public_key']))
            return bool(config.get('seller_name') and re.fullmatch(
                r'[^\s@]+@[^\s@]+\.[^\s@]+', config.get('support_email', '')))
        except ValueError:
            return False
    if not config or not all(config.get(k) for k in FIELDS):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(decode(config['public_key']))
        return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', config['support_email'])
                    and re.fullmatch(r'[A-Z0-9]{8}([A-Z0-9]{3})?', config['swift_bic']))
    except ValueError:
        return False


def verify(token, public_key, installation):
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise ValueError()
        prefix, payload, signature = token.strip().split('.')
        if prefix != 'CP1':
            raise ValueError()
        raw = decode(payload)
        Ed25519PublicKey.from_public_bytes(decode(public_key)).verify(decode(signature), raw)
        data = json.loads(raw)
        plan = PLANS.get(data.get('product')) if isinstance(data, dict) and isinstance(data.get('product'), str) else None
        if (not isinstance(data, dict) or plan is None
                or data.get('installation') != installation
                or type(data.get('documents')) is not int or data['documents'] != plan[2]
                or type(data.get('amount_cents')) is not int or data['amount_cents'] != plan[0]
                or data.get('currency') != plan[1]
                or not re.fullmatch(r'[a-f0-9]{32}', str(data.get('id', '')))
                or not isinstance(data.get('customer'), str) or not 1 <= len(data['customer']) <= 200
                or type(data.get('issued_at')) is not int or data['issued_at'] > time.time() + 300):
            raise ValueError()
        return data
    except (ValueError, TypeError, InvalidSignature, KeyError) as error:
        raise ValueError('This activation key is invalid or belongs to another installation.') from error


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LicenseStore:
    def __init__(self, config_path, data_root):
        self.config_path = Path(config_path)
        self.root = Path(data_root) / 'billing'
        self.lock = threading.RLock()
        self.admin_session = None

    def config(self):
        config = configuration(self.config_path)
        if getattr(sys, 'frozen', False):
            if (config is None or not FROZEN_CONFIG_HASH
                    or hashlib.sha256(self.config_path.read_bytes()).hexdigest() != FROZEN_CONFIG_HASH
                    or config.get('payment_provider') != 'razorpay'):
                raise ValueError('Payment configuration is missing or changed. Reinstall the seller-provided application.')
        return config

    def installation(self):
        config = self.config()
        if config and config.get('payment_provider') == 'razorpay':
            from clearpage.billing.online_credits import identity
            return identity(self.root)['installation']
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'installation.txt'
        try:
            with path.open('x', encoding='ascii') as stream:
                stream.write('CP-' + secrets.token_hex(16))
        except FileExistsError:
            pass
        value = path.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'CP-[a-f0-9]{32}', value):
            raise ValueError('Installation identity is damaged. Restore your billing backup or contact the seller.')
        return value

    def state(self, config):
        path = self.root / 'credits.json'
        if not path.exists():
            return {'tokens': [], 'documents': []}
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError()
            state = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(state, dict) or set(state) != {'tokens', 'documents'}
                    or not isinstance(state['tokens'], list) or not isinstance(state['documents'], list)
                    or len(state['tokens']) > 1000):
                raise ValueError()
            packs = [verify(t, config['public_key'], self.installation()) for t in state['tokens']]
            ids = [pack['id'] for pack in packs]
            docs = state['documents']
            if (len(set(ids)) != len(ids) or any(not isinstance(d, str) or not re.fullmatch(r'[a-f0-9]{64}', d) for d in docs)
                    or len(set(docs)) != len(docs) or len(docs) > sum(pack['documents'] for pack in packs)):
                raise ValueError()
            return state
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise ValueError('Saved credits could not be verified. Restore your billing backup or contact the seller.') from error

    def status(self):
        with self.lock:
            result = dict(enabled=getattr(sys, 'frozen', False) or self.config_path.exists(), payment_ready=False, credits=0,
                          price=f'{PRICE_MINOR / 100:.2f}', currency=CURRENCY, amount_minor=PRICE_MINOR,
                          documents_per_pack=DOCUMENTS)
            try:
                config = self.config()
                if config is None:
                    return result
                result['installation'] = self.installation()
                result['payment_provider'] = config.get('payment_provider') or 'manual'
                if result['payment_provider'] == 'razorpay':
                    result.update(online_required=True, payment_ready=payment_ready(config))
                    if result['payment_ready']:
                        result['payment'] = {k: config[k] for k in ('seller_name', 'support_email')}
                        from clearpage.billing.online_credits import query
                        balance = query(self, config)
                        result.update({k: balance[k] for k in ('credits', 'used', 'purchased')})
                        result.update(admin=balance.get('admin') is True,
                                      admin_expires_at=balance.get('admin_expires_at', 0))
                    return result
                state = self.state(config)
                purchased = self.purchased(state, config)
                result.update(credits=purchased - len(state['documents']),
                              purchased=purchased, used=len(state['documents']),
                              payment_ready=payment_ready(config))
                if result['payment_ready']:
                    fields = ('seller_name', 'support_email') if result['payment_provider'] == 'razorpay' else FIELDS
                    result['payment'] = {k: config[k] for k in fields if k != 'public_key'}
            except (ValueError, OSError) as error:
                result['error'] = str(error) if isinstance(error, ValueError) else 'Unable to access saved credits.'
            return result

    def activate(self, token):
        with self.lock:
            config = self.config()
            if config and config.get('payment_provider') == 'razorpay':
                raise ValueError('This edition uses online credits. Use Check payment; offline activation keys cannot add credits.')
            if config is None or not config.get('public_key'):
                raise ValueError('License activation is not configured yet. Contact the seller.')
            token = token.strip() if isinstance(token, str) else token
            verified = verify(token, config['public_key'], self.installation())
            state = self.state(config)
            ids = [verify(t, config['public_key'], self.installation())['id'] for t in state['tokens']]
            if verified['id'] not in ids:
                if len(ids) >= 1000:
                    raise ValueError('Please contact the seller before adding more packs.')
                state['tokens'].append(token)
                atomic_json(self.root / 'credits.json', state)
            return self.status()

    @staticmethod
    def fingerprint(source):
        with Path(source).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    def purchased(self, state, config):
        return sum(verify(t, config['public_key'], self.installation())['documents'] for t in state['tokens'])

    def document(self, source, consume=False):
        with self.lock:
            config = self.config()
            if config is None:
                return
            if config.get('payment_provider') == 'razorpay':
                from clearpage.billing.online_credits import query
                query(self, config, self.fingerprint(source), consume)
                return
            state = self.state(config)
            digest = self.fingerprint(source)
            if digest in state['documents']:
                return
            if not consume or len(state['documents']) >= self.purchased(state, config):
                raise LicenseRequired('A document credit is required to upload this PDF. Previously paid files can be reopened without another credit.')
            state['documents'].append(digest)
            atomic_json(self.root / 'credits.json', state)


def register(a):
    from flask import jsonify, request
    config_path = str(a.BASE / 'billing.json') if getattr(sys, 'frozen', False) else os.environ.get('CLEARPAGE_BILLING_CONFIG', str(a.BASE / 'billing.json'))
    store = LicenseStore(config_path,
                         a.WORK['data_root'])
    a.BILLING = store
    from clearpage.billing.payment_client import register_payments
    register_payments(a.app, store)
    from clearpage.billing.admin_client import register_admin_client
    register_admin_client(a.app, store)

    @a.app.errorhandler(LicenseRequired)
    def required(error):
        return jsonify(error=str(error), code='license_required'), 402

    @a.app.get('/api/billing')
    def billing_status():
        return jsonify(store.status())

    @a.app.post('/api/billing/activate')
    def billing_activate():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('Paste an activation key.')
        return jsonify(store.activate(data.get('key')))

    @a.app.before_request
    def protect_documents():
        identifier = (request.view_args or {}).get('identifier')
        if identifier in a.JOBS and not request.path.startswith(('/api/clear/', '/api/cancel/')):
            store.document(a.JOBS[identifier]['source'])
        if request.path == '/api/queue' and request.method == 'POST':
            data = request.get_json(silent=True)
            ids = data.get('ids', []) if isinstance(data, dict) else []
            if isinstance(ids, list):
                for key in ids:
                    if isinstance(key, str) and key in a.JOBS:
                        store.document(a.JOBS[key]['source'])
        if request.path == '/api/queue/download':
            for job in a.JOBS.values():
                store.document(job['source'])
