"""Owner-only CLI. Keep this tool, signing key and payment ledger private."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from clearpage.billing.licensing import DOCUMENTS, PRICE_MINOR, PRODUCT, CURRENCY, atomic_json, encode


def keygen(private_path, public_path, password):
    private_path, public_path = Path(private_path), Path(public_path)
    if private_path.resolve().is_relative_to(ROOT):
        raise ValueError('Store the private signing key outside the application repository.')
    if private_path.exists() or public_path.exists():
        raise ValueError('A key file already exists. Never overwrite an active signing key.')
    if len(password) < 12:
        raise ValueError('Use a signing-key password with at least 12 characters.')
    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    with private_path.open('xb') as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.BestAvailableEncryption(password.encode())))
    with public_path.open('x', encoding='ascii') as stream:
        stream.write(encode(key.public_key().public_bytes_raw()) + '\n')


def issue(private_path, password, installation, customer, payment_reference, confirmed):
    if not confirmed:
        raise ValueError('Verify the credited payment in your bank before using --confirm-received.')
    if not re.fullmatch(r'CP-[a-f0-9]{32}', installation):
        raise ValueError('Use the installation code shown in the customer application.')
    customer, payment_reference = customer.strip(), payment_reference.strip()
    if not 1 <= len(customer) <= 200 or not 1 <= len(payment_reference) <= 200:
        raise ValueError('Provide a customer and a unique bank transaction reference (up to 200 characters).')
    path = Path(private_path)
    key = serialization.load_pem_private_key(path.read_bytes(), password=password.encode())
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Not a ClearPage signing key.')
    ledger_path = path.with_name(path.stem + '-issued.json')
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
    ref = hashlib.sha256(payment_reference.encode()).hexdigest()
    if ref in ledger:
        entry = ledger[ref]
        if entry['installation'] != installation or entry['customer'] != customer:
            raise ValueError('This payment reference was already used for another customer or installation.')
        return entry['token']
    # Stable pack identity means reissuing the same payment cannot add more credits.
    identity = hashlib.sha256(key.public_key().public_bytes_raw() + payment_reference.encode()).hexdigest()[:32]
    data = dict(product=PRODUCT, id=identity, installation=installation, customer=customer,
                documents=DOCUMENTS, amount_cents=PRICE_MINOR, currency=CURRENCY, issued_at=int(time.time()))
    payload = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    token = 'CP1.' + encode(payload) + '.' + encode(key.sign(payload))
    ledger[ref] = dict(installation=installation, customer=customer, token=token,
                       payment_reference=payment_reference, issued_at=data['issued_at'])
    atomic_json(ledger_path, ledger)
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('keygen', help='Create an encrypted private key and public verification key.')
    create.add_argument('--private-key', required=True)
    create.add_argument('--public-key', required=True)
    activate = commands.add_parser('issue', help='Issue one $99 / two-document pack after checking your bank.')
    activate.add_argument('--private-key', required=True)
    activate.add_argument('--installation', required=True)
    activate.add_argument('--customer', required=True)
    activate.add_argument('--payment-reference', required=True)
    activate.add_argument('--confirm-received', action='store_true')
    activate.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        password = getpass.getpass('Signing-key password: ')
        if args.command == 'keygen':
            if password != getpass.getpass('Confirm signing-key password: '):
                raise ValueError('Passwords do not match.')
            keygen(args.private_key, args.public_key, password)
            print('Keys created. Put only the public key in billing.json. Back up the private key securely.')
        else:
            token = issue(args.private_key, password, args.installation, args.customer,
                          args.payment_reference, args.confirm_received)
            output = Path(args.output)
            if output.exists() and output.read_text(encoding='utf-8').strip() != token:
                raise ValueError('Output already contains a different activation key. Choose another filename.')
            output.write_text(token + '\n', encoding='utf-8')
            print('Activation key saved. Send this file to the customer. It adds two document credits once.')
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
