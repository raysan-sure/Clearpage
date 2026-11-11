"""Owner access cannot be enabled by client flags or copied to another installation."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from tests import test_razorpay
from payment_service.server import create_app
from clearpage.billing.licensing import LicenseStore, atomic_json, LicenseRequired
from clearpage.billing.online_credits import identity, query
from clearpage.billing.admin_client import register_admin_client
from scripts.setup_test_payments import save_settings

PASSWORD='Synthetic owner test password 123!'
PASSWORD_HASH=generate_password_hash(PASSWORD,method='scrypt:32768:8:1')


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_razorpay.RazorpayTests();self.fixture.setUp()
        self.settings={**self.fixture.settings,'CLEARPAGE_ADMIN_USERNAME':'owner@example.test',
            'CLEARPAGE_ADMIN_PASSWORD_HASH':PASSWORD_HASH}
        self.service=create_app(self.settings);self.client=self.service.test_client()
        self.account=dict(account_secret=self.fixture.account_secret,installation=self.fixture.installation)
    def tearDown(self):self.fixture.tearDown()
    def login(self,**changes):
        return self.client.post('/api/admin/login',json={**self.account,'username':'owner@example.test','password':PASSWORD,**changes})
    def credit(self,session=None,digest=None,consume=False,**changes):
        body={**self.account,'nonce':secrets.token_hex(16),'consume':consume,**changes}
        if session:body['admin_session']=session
        if digest:body['digest']=digest
        response=self.client.post('/api/credits',json=body)
        from clearpage.billing.licensing import decode
        return json.loads(decode(response.json['payload']))
    def test_owner_can_use_many_documents_without_creating_or_spending_credits(self):
        session=self.login().json['session']
        for i in range(4):
            result=self.credit(session,hashlib.sha256(str(i).encode()).hexdigest(),True)
            self.assertTrue(result['authorized']);self.assertTrue(result['admin']);self.assertEqual(result['credits'],0)
        with closing(sqlite3.connect(self.settings['CLEARPAGE_PAYMENT_DB'])) as db,db:
            self.assertEqual(db.execute('SELECT count(*) FROM document_access').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM purchases').fetchone()[0],0)
        self.assertEqual(self.fixture.calls,[])
    def test_forged_client_flags_and_wrong_credentials_do_not_grant_access(self):
        for changes in (dict(password='wrong'),dict(username='someone@example.test'),dict(username='☃')):
            self.assertEqual(self.login(**changes).status_code,401)
        result=self.credit('a'*64,'b'*64,True,admin=True,role='admin')
        self.assertFalse(result['admin']);self.assertFalse(result['authorized'])
        self.assertEqual(self.fixture.client.post('/api/admin/login',json={**self.account,'username':'owner','password':PASSWORD}).status_code,401)
    def test_sessions_are_hashed_and_bound_to_installation_and_expiry(self):
        session=self.login().json['session']
        other_secret='e'*64
        other=dict(account_secret=other_secret,installation='CP-'+hashlib.sha256(other_secret.encode()).hexdigest()[:32])
        self.assertFalse(self.credit(session,**other)['admin'])
        with closing(sqlite3.connect(self.settings['CLEARPAGE_PAYMENT_DB'])) as db,db:
            row=db.execute('SELECT token_hash FROM admin_sessions').fetchone()[0]
            self.assertNotEqual(row,session);self.assertEqual(row,hashlib.sha256(session.encode()).hexdigest())
            db.execute('UPDATE admin_sessions SET expires=0')
        self.assertFalse(self.credit(session,'b'*64,True)['authorized'])
    def test_logout_revokes_session_and_other_installation_cannot_revoke_it(self):
        session=self.login().json['session']
        other_secret='e'*64
        other=dict(account_secret=other_secret,installation='CP-'+hashlib.sha256(other_secret.encode()).hexdigest()[:32])
        self.client.post('/api/admin/logout',json={**other,'admin_session':session})
        self.assertTrue(self.credit(session)['admin'])
        self.client.post('/api/admin/logout',json={**self.account,'admin_session':session})
        self.assertFalse(self.credit(session,'b'*64,True)['authorized'])
    def test_credential_change_and_new_login_invalidate_old_sessions(self):
        old=self.login().json['session'];new=self.login().json['session']
        self.assertFalse(self.credit(old)['admin']);self.assertTrue(self.credit(new)['admin'])
        changed=create_app({**self.settings,'CLEARPAGE_ADMIN_PASSWORD_HASH':generate_password_hash('A different synthetic password',method='scrypt:32768:8:1')})
        response=changed.test_client().post('/api/credits',json={**self.account,'nonce':secrets.token_hex(16),'admin_session':new})
        from clearpage.billing.licensing import decode
        self.assertFalse(json.loads(decode(response.json['payload']))['admin'])
    def test_login_rate_limit_and_invalid_server_configuration(self):
        for _ in range(5):self.assertEqual(self.login(password='wrong').status_code,401)
        self.assertEqual(self.login(password='wrong').status_code,429)
        with self.assertRaises(ValueError):create_app({**self.settings,'CLEARPAGE_ADMIN_PASSWORD_HASH':'plaintext-not-a-hash'})
    def test_desktop_login_is_in_memory_and_logout_restores_customer_gate(self):
        with test_razorpay.running(self.service) as url,patch.dict('os.environ',{'CLEARPAGE_PAYMENT_LOCAL_TEST':'1'}):
            config=self.fixture.root/'billing.json'
            atomic_json(config,dict(payment_provider='razorpay',payment_service_url=url,public_key=self.fixture.public,
                seller_name='Test seller',support_email='owner@example.test'))
            store=LicenseStore(config,self.fixture.root/'desktop')
            local=Flask('admin-desktop');register_admin_client(local,store)
            local.register_error_handler(ValueError,lambda error:(jsonify(error='Access denied'),400))
            client=local.test_client()
            with patch('clearpage.billing.online_credits.json_request',return_value={'payload':'bad','signature':'bad'}),patch('clearpage.billing.admin_client.json_request') as send_password:
                response=client.post('/api/admin/login',json=dict(username='owner@example.test',password=PASSWORD))
                self.assertEqual(response.status_code,400)
                send_password.assert_not_called()
            response=client.post('/api/admin/login',json=dict(username='owner@example.test',password=PASSWORD))
            self.assertEqual(response.status_code,200);self.assertTrue(response.json['admin'])
            self.assertNotIn('session',response.json)
            token=store.admin_session
            for i in range(4):
                source=self.fixture.root/f'document-{i}.pdf';source.write_bytes(str(i).encode())
                store.document(source,consume=True)
            for file in store.root.rglob('*'):
                if file.is_file():self.assertNotIn(token,file.read_text())
            self.assertEqual(client.post('/api/admin/logout',json={}).status_code,200)
            self.assertIsNone(store.admin_session)
            with self.assertRaises(LicenseRequired):store.document(source,consume=True)
    def test_customer_purchases_are_not_changed_by_admin_use(self):
        self.fixture.create();self.fixture.captured();self.fixture.confirm()
        session=self.login().json['session']
        self.assertEqual(self.credit(session,'b'*64,True)['credits'],2)
        self.client.post('/api/admin/logout',json={**self.account,'admin_session':session})
        result=self.credit(digest='b'*64,consume=True)
        self.assertFalse(result['admin']);self.assertEqual(result['credits'],1)
    def test_desktop_endpoints_keep_cross_site_protection(self):
        import app
        self.assertEqual(app.app.test_client().post('/api/admin/login',json={'username':'owner','password':PASSWORD}).status_code,403)
        self.assertEqual(app.app.test_client().post('/api/admin/logout',json={},headers={'Origin':'https://other.example','X-ClearPage':'local'}).status_code,403)


class PrivateTestSetupTests(unittest.TestCase):
    def test_saves_test_keys_and_hashed_admin_password_outside_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            save_settings(path,'rzp_test_synthetic','synthetic-secret','owner@example.test',PASSWORD,'sales@zynape.com')
            settings=json.loads(path.read_text());public=(path.parent/'billing.json').read_text()
            self.assertTrue(check_password_hash(settings['CLEARPAGE_ADMIN_PASSWORD_HASH'],PASSWORD))
            self.assertNotIn(PASSWORD,path.read_text());self.assertNotIn('synthetic-secret',public)
            self.assertNotIn('PASSWORD_HASH',public)
            self.assertIn(b'ENCRYPTED PRIVATE KEY',(path.parent/'signing.pem').read_bytes())
            for key,password in (('rzp_live_synthetic',PASSWORD),('rzp_test_synthetic','short')):
                with self.assertRaises(ValueError):save_settings(path,key,'synthetic-secret','owner@example.test',password,'sales@zynape.com')
