"""Online credit authority client. No offline balance or usage is trusted."""
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from clearpage.billing.licensing import atomic_json, decode, LicenseRequired
from clearpage.billing.payment_http import json_request


def identity(root):
    path = Path(root) / 'online-account.json'
    if not path.exists():
        atomic_json(path, {'secret': secrets.token_hex(32)})
    try:
        value = json.loads(path.read_text(encoding='utf-8'))['secret']
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
            raise ValueError()
        return {'installation': 'CP-' + hashlib.sha256(value.encode()).hexdigest()[:32], 'account_secret': value}
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError('Online identity is damaged. Restore your profile backup or contact support.') from error


def query(store, config, digest=None, consume=False):
    from clearpage.billing.payment_client import service_url
    if not config or not config.get('public_key'):
        raise ValueError('Online credit service is not configured. Contact the seller.')
    account = identity(store.root)
    nonce = secrets.token_hex(16)
    body = {**account, 'nonce': nonce}
    session = getattr(store, 'admin_session', None)
    if session:
        body['admin_session'] = session
    if digest is not None:
        body.update(digest=digest, consume=consume)
    response = json_request(service_url(config) + '/api/credits', body)
    try:
        raw = decode(response['payload'])
        Ed25519PublicKey.from_public_bytes(decode(config['public_key'])).verify(decode(response['signature']), raw)
        result = json.loads(raw)
        if (result['installation'] != account['installation'] or result['nonce'] != nonce
                or abs(time.time() - result['issued_at']) > 120
                or result.get('digest') != digest
                or any(type(result[k]) is not int or result[k] < 0 for k in ('credits', 'used', 'purchased'))):
            raise ValueError()
        if result.get('admin') is True and (not session or type(result.get('admin_expires_at')) is not int
                                          or result['admin_expires_at']<=time.time()):
            raise ValueError()
    except Exception as error:
        raise ValueError('The online credit response could not be verified. No access was granted.') from error
    if session and result.get('admin') is not True:
        store.admin_session = None
    if digest is not None and result.get('authorized') is not True:
        raise LicenseRequired('A document credit is required to open this PDF. Paid originals can be reopened without another credit.')
    return result
