"""Render entry point: public website plus private-state payment/credit API."""
import os
from pathlib import Path
from flask import send_from_directory, jsonify, redirect
from payment_service.server import create_app


def hosted_app():
    app = create_app()
    website = Path(__file__).resolve().parents[1] / 'website'

    @app.get('/')
    def home():
        return send_from_directory(website, 'index.html')

    @app.get('/<name>')
    def public_file(name):
        if name not in ('policies.html', 'style.css', 'enquiry.js', 'download.js'):
            return jsonify(error='Not found'), 404
        return send_from_directory(website, name)

    @app.get('/downloads/ClearPage-Windows-x64.zip')
    def download():
        # Compatibility URL: follow GitHub's latest published stable release.
        # No customer credentials, private files, or stale local ZIP are served.
        response = redirect('https://github.com/raysan-sure/Clearpage/releases/latest/download/ClearPage-Windows-x64.zip', code=302)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    return app


if __name__ == '__main__':
    from waitress import serve
    app = hosted_app()
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT', '10000')), threads=8)
