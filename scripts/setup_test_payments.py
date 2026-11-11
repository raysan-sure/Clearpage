"""Private local setup window. Never prints, uploads, or commits credentials."""
import argparse
import json
from pathlib import Path
import re
import secrets
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from werkzeug.security import generate_password_hash
from clearpage.billing.licensing import atomic_json, encode


def save_settings(path, key_id, key_secret, username, password, support_email):
    path=Path(path).resolve()
    if path.is_relative_to(ROOT):
        raise ValueError('Choose a private settings file outside the application repository.')
    settings=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    key_id=key_id.strip() or settings.get('RAZORPAY_KEY_ID','')
    key_secret=key_secret.strip() or settings.get('RAZORPAY_KEY_SECRET','')
    username=username.strip().casefold()
    if not re.fullmatch(r'rzp_test_[A-Za-z0-9]+',key_id) or not 8<=len(key_secret)<=200:
        raise ValueError('Enter a Razorpay TEST Key ID (rzp_test_...) and its Key Secret. Live keys are not accepted here.')
    if not re.fullmatch(r'[a-z0-9@._+-]{3,100}',username):
        raise ValueError('Use an admin username/email containing 3–100 letters, numbers, @, dots, +, - or _.')
    if password and not 15<=len(password)<=200:
        raise ValueError('Choose an admin password with at least 15 characters (maximum 200).')
    if not password and not settings.get('CLEARPAGE_ADMIN_PASSWORD_HASH'):
        raise ValueError('Choose your admin password. No default password is provided.')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',support_email.strip()):
        raise ValueError('Enter your support email address.')
    existing_db=Path(settings.get('CLEARPAGE_PAYMENT_DB',str(path.parent/'payments.sqlite3')))
    if settings.get('RAZORPAY_KEY_ID') and settings['RAZORPAY_KEY_ID']!=key_id and existing_db.is_file():
        raise ValueError('This test database belongs to another key. Use a separate private test folder rather than replacing an existing identity.')
    path.parent.mkdir(parents=True,exist_ok=True)
    signing=Path(settings.get('CLEARPAGE_SIGNING_KEY') or path.parent/'signing.pem')
    signing_password=settings.get('CLEARPAGE_SIGNING_PASSWORD') or secrets.token_urlsafe(32)
    if signing.is_file():
        signing_key=serialization.load_pem_private_key(signing.read_bytes(),password=signing_password.encode())
        if not isinstance(signing_key,Ed25519PrivateKey):raise ValueError('An Ed25519 signing key is required.')
    else:
        if existing_db.is_file():raise ValueError('Restore the existing signing key before using this database.')
        signing_key=Ed25519PrivateKey.generate()
        with signing.open('xb') as stream:
            stream.write(signing_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(signing_password.encode())))
    public=encode(signing_key.public_key().public_bytes_raw())
    settings.update(RAZORPAY_KEY_ID=key_id,RAZORPAY_KEY_SECRET=key_secret,
        RAZORPAY_WEBHOOK_SECRET=settings.get('RAZORPAY_WEBHOOK_SECRET') or secrets.token_urlsafe(32),
        CLEARPAGE_SIGNING_KEY=str(signing),CLEARPAGE_SIGNING_PASSWORD=signing_password,
        CLEARPAGE_PAYMENT_DB=str(existing_db.resolve()),CLEARPAGE_SELLER_NAME='Raysan sure (TEST)',
        CLEARPAGE_SUPPORT_EMAIL=support_email.strip(),CLEARPAGE_ADMIN_USERNAME=username)
    if password:settings['CLEARPAGE_ADMIN_PASSWORD_HASH']=generate_password_hash(password,method='scrypt:32768:8:1')
    atomic_json(path,settings)
    atomic_json(path.parent/'billing.json',dict(payment_provider='razorpay',payment_service_url='http://127.0.0.1:5056',
        seller_name=settings['CLEARPAGE_SELLER_NAME'],support_email=settings['CLEARPAGE_SUPPORT_EMAIL'],public_key=public))
    (path.parent/'public-key.txt').write_text(public+'\n',encoding='ascii')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    path=args.config.resolve()
    if path.is_relative_to(ROOT):parser.error('Keep private settings outside the repository.')
    import tkinter as tk
    from tkinter import ttk, messagebox
    settings=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    window=tk.Tk();window.title('ClearPage — Private TEST setup');window.geometry('640x610')
    if (ROOT/'assets/clearpage.ico').is_file():window.iconbitmap(str(ROOT/'assets/clearpage.ico'))
    window.minsize(610,590)
    frame=ttk.Frame(window,padding=26);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Razorpay test keys + owner login',font=('Segoe UI',17,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Local setup only. This window does not upload credentials or collect money.',wraplength=560).pack(anchor='w',pady=(8,16))
    values={}
    fields=[('key_id','Razorpay Test Key ID (rzp_test_...)',settings.get('RAZORPAY_KEY_ID',''),False),
        ('key_secret','Razorpay Key Secret — leave blank only if already saved','',True),
        ('username','Your ClearPage admin username',settings.get('CLEARPAGE_ADMIN_USERNAME','sales@zynape.com'),False),
        ('password','Choose admin password (at least 15 characters)','',True),
        ('confirm','Confirm admin password','',True),
        ('support_email','Support email','sales@zynape.com',False)]
    for name,label,initial,secret in fields:
        ttk.Label(frame,text=label).pack(anchor='w',pady=(7,3))
        value=tk.StringVar(value=initial);values[name]=value
        ttk.Entry(frame,textvariable=value,show='*' if secret else '',width=66).pack(fill='x')
    status=tk.StringVar(value='Use Razorpay TEST keys. Never enter live keys or bank/card details here.')
    ttk.Label(frame,textvariable=status,wraplength=560).pack(anchor='w',pady=16)
    def save():
        if values['password'].get()!=values['confirm'].get():
            messagebox.showerror('Check password','The admin passwords do not match.',parent=window);return
        try:
            save_settings(path,**{name:value.get() for name,value in values.items() if name!='confirm'})
        except (ValueError,OSError,TypeError):
            # The detailed validation message contains no submitted secret values.
            error=sys.exc_info()[1]
            messagebox.showerror('Setup not saved',str(error) if isinstance(error,ValueError) else 'Unable to save private settings.',parent=window);return
        for name in ('key_secret','password','confirm'):values[name].set('')
        status.set('Saved privately. Your admin password is hashed; it is not stored as text. Close this window and tell Codex “Test setup saved”.')
    ttk.Button(frame,text='Save private TEST settings',command=save).pack(anchor='w')
    window.mainloop()


if __name__=='__main__':main()
