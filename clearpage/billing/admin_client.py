"""Owner sessions stay in memory; every document still needs server authorization."""
import re
from flask import jsonify, request
from clearpage.billing.payment_http import json_request


def register_admin_client(app, store):
    from clearpage.billing.online_credits import identity, query
    from clearpage.billing.payment_client import service_url
    from clearpage.billing.licensing import payment_ready

    @app.post('/api/admin/login')
    def login():
        value = request.get_json(silent=True)
        if (not isinstance(value, dict) or not isinstance(value.get('username'), str)
                or not isinstance(value.get('password'), str) or len(value['username'])>100
                or not 1<=len(value['password'])<=200):
            raise ValueError('Enter your admin username and password.')
        with store.lock:
            config = store.config()
            if not config or config.get('payment_provider')!='razorpay' or not payment_ready(config):
                raise ValueError('Configure the seller service before signing in as admin.')
            # Authenticate the service's signing key before sending the owner password,
            # including in the explicitly allowed localhost test environment.
            query(store,config)
            try:
                result=json_request(service_url(config)+'/api/admin/login',
                    {**identity(store.root),'username':value['username'],'password':value['password']})
            except ValueError as error:
                raise ValueError('Admin sign-in failed. Check your credentials and connection.') from error
            token=result.get('session')
            if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{64}',token):
                raise ValueError('Admin session could not be verified.')
            store.admin_session=token
            try:
                balance=query(store,config)
                if balance.get('admin') is not True:
                    raise ValueError('Admin session could not be verified.')
            except Exception:
                store.admin_session=None
                raise
            return jsonify(store.status())

    @app.post('/api/admin/logout')
    def logout():
        with store.lock:
            token=store.admin_session
            store.admin_session=None  # Drop local privileges even if the service is offline.
            if token:
                try:
                    config=store.config()
                    json_request(service_url(config)+'/api/admin/logout',
                        {**identity(store.root),'admin_session':token})
                except ValueError:
                    return jsonify(billing=store.status(),message='Signed out locally. The server session expires within one hour; remote revocation could not be confirmed.')
            return jsonify(billing=store.status(),message='Signed out. Customer document credits apply again.')
