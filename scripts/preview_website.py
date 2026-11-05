"""Serve the public website and an explicitly selected testing ZIP on loopback only."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD = '/downloads/ClearPage-preview-Windows-x64.zip'


class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.serve_preview(head=False)

    def do_HEAD(self):
        self.serve_preview(head=True)

    def serve_preview(self, head):
        path = urlsplit(self.path).path
        if path in ('/', '/index.html'):
            page = (ROOT/'website/index.html').read_text(encoding='utf-8')
            page = page.replace('https://github.com/raysan-sure/Clearpage/releases/latest/download/ClearPage-Windows-x64.zip', DOWNLOAD)
            page = page.replace('Download latest for Windows', 'Download preview for Windows')
            page = page.replace('Latest published stable release', 'Local preview build · Payments disabled')
            body = page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if not head:
                self.wfile.write(body)
        elif path == DOWNLOAD:
            with self.server.archive.open('rb') as source:
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Length', str(self.server.archive.stat().st_size))
                self.send_header('Content-Disposition', 'attachment; filename="ClearPage-preview-Windows-x64.zip"')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                if not head:
                    shutil.copyfileobj(source, self.wfile)
        else:
            if head:
                super().do_HEAD()
            else:
                super().do_GET()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--port', type=int, default=5173)
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    if not zipfile.is_zipfile(archive):
        parser.error('Choose a built preview ZIP.')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(PreviewHandler, directory=str(ROOT/'website')))
    server.archive = archive
    print(f'Local website and testing download: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
