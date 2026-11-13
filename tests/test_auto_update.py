"""Synthetic releases exercise download, transactional replacement and recovery."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

from clearpage import auto_update, update_install, updates
from scripts.prepare_ci_version import next_version


def package(version='99.0', extra=None):
    files = {'ClearPage.exe': b'new executable', 'ClearPageUpdater.exe': b'helper',
             'ClearPage.exe.config': b'config', '_internal/static/ui.js': b'new interface'}
    files['package-manifest.json'] = json.dumps(dict(format=1, edition='Standard', version=version,
        entries=[name for name in update_install.ENTRIES if name == 'package-manifest.json'
                 or name in files or any(p.startswith(name+'/') for p in files)])).encode()
    memory = BytesIO()
    with zipfile.ZipFile(memory, 'w') as archive:
        for name, data in files.items():
            archive.writestr('ClearPage/'+name, data)
        if extra:
            archive.writestr(extra, b'unsafe')
    return memory.getvalue()


def release(data):
    return dict(version='99.0', available=True, message='New version', size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), download_url=f'https://github.com/{updates.REPOSITORY}/releases/download/v99.0/ClearPage-99.0-Windows-x64.zip')


@contextmanager
def asset_server(body, status=200):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def open_asset(request, timeout):
        return transport.open(f'http://127.0.0.1:{server.server_port}/asset', timeout=timeout)
    try:
        yield open_asset, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class AutomaticUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_real_http_download_checksum_and_archive(self):
        data = package()
        target = self.root/'update.zip'
        with asset_server(data) as (opener, calls):
            auto_update.download(release(data), target, opener=opener)
            self.assertEqual(calls, ['/asset'])
        stage = auto_update.extract(target, self.root/'new', '99.0')
        self.assertEqual((stage/'ClearPage.exe').read_bytes(), b'new executable')

    def test_http_errors_truncation_and_corruption_never_leave_a_package(self):
        data = package()
        target = self.root/'update.zip'
        for body, status in ((b'error page', 500), (data[:-7], 200), (data+b'extra', 200), (b'x'*len(data), 200)):
            with self.subTest(status=status, size=len(body)), asset_server(body, status) as (opener, calls):
                with self.assertRaises((OSError, ValueError)):
                    auto_update.download(release(data), target, opener=opener)
                self.assertEqual(calls, ['/asset'])
                self.assertFalse(target.exists())
        with self.assertRaises(TimeoutError):
            auto_update.download(release(data), target, opener=lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
        self.assertFalse(target.exists())

    def test_untrusted_metadata_is_rejected_before_network_access(self):
        for change in (dict(sha256=None), dict(sha256='wrong'), dict(size=auto_update.MAX_DOWNLOAD+1),
                       dict(download_url='https://evil.example/update.zip')):
            with patch('urllib.request.build_opener') as network, self.assertRaises(ValueError):
                auto_update.download({**release(package()), **change}, self.root/'download')
            network.assert_not_called()
        redirect = auto_update.GitHubRedirects()
        for url in ('http://github.com/file', 'https://evil.example/file', 'https://github.com.evil.example/file'):
            with self.assertRaises(ValueError):
                redirect.redirect_request(None, None, 302, '', {}, url)

    def test_archive_traversal_aliases_duplicates_and_wrong_version_rejected(self):
        for index, extra in enumerate(('../outside.txt', 'ClearPage/../outside.txt',
                'ClearPage/_internal/evil:stream', 'ClearPage/_internal/CON.txt',
                'ClearPage/_internal/name.', 'ClearPage/clearpage.exe', '/absolute/file',
                'ClearPage/./ClearPage.exe', 'ClearPage//ClearPage.exe')):
            path = self.root/f'{index}.zip'
            path.write_bytes(package(extra=extra))
            with self.assertRaises(ValueError):
                auto_update.extract(path, self.root/f'extracted-{index}', '99.0')
            self.assertFalse((self.root/f'extracted-{index}').exists())
        path = self.root/'version.zip'
        path.write_bytes(package())
        with self.assertRaises(ValueError):
            auto_update.extract(path, self.root/'wrong-version', '100.0')

    def plan(self):
        install = self.root/'ClearPage'
        install.mkdir()
        (install/'ClearPage.exe').write_bytes(b'old executable')
        (install/'_internal').mkdir()
        (install/'_internal/old.dll').write_bytes(b'old library')
        (install/'customer.pdf').write_bytes(b'user document')
        work = self.root/'.clearpage-update-test'
        work.mkdir()
        archive = work/'download.zip'
        archive.write_bytes(package())
        stage = auto_update.extract(archive, work/'new', '99.0')
        plan = work/'plan.json'
        update_install.write_json(plan, dict(install=str(install), stage=str(stage), version='99.0', token='a'*48))
        return install, work, plan

    def child(self, code=None):
        return SimpleNamespace(pid=1234, poll=lambda: code, wait=lambda **kw: 0, terminate=lambda: None)

    def test_install_preserves_user_files_and_retains_previous_application(self):
        install, work, plan = self.plan()
        def launch(path, health=None, token=None):
            update_install.write_json(health, dict(token=token, version='99.0', ui_ready=True))
            return self.child()
        self.assertTrue(update_install.apply(plan, launch=launch, health_timeout=1))
        self.assertEqual((install/'ClearPage.exe').read_bytes(), b'new executable')
        self.assertEqual((install/'customer.pdf').read_bytes(), b'user document')
        self.assertEqual((work/'backup/ClearPage.exe').read_bytes(), b'old executable')
        self.assertTrue(json.loads((work/'result.json').read_text())['installed'])

    def test_failed_desktop_rolls_back_and_restarts_old_application(self):
        install, work, plan = self.plan()
        starts = []
        def launch(path, health=None, token=None):
            starts.append((path/'ClearPage.exe').read_bytes())
            return self.child(1)
        self.assertFalse(update_install.apply(plan, launch=launch, health_timeout=1))
        self.assertEqual(starts, [b'new executable', b'old executable'])
        self.assertEqual((install/'_internal/old.dll').read_bytes(), b'old library')
        self.assertEqual((install/'customer.pdf').read_bytes(), b'user document')
        self.assertFalse((install/'package-manifest.json').exists())
        self.assertTrue(json.loads((work/'result.json').read_text())['rolled_back'])

    def test_file_replacement_failure_rolls_back_partial_install(self):
        install, work, plan = self.plan()
        original_move = update_install.move
        def fail_once(source, target):
            if source == work/'new/ClearPage/ClearPage.exe':
                raise PermissionError('synthetic file lock')
            original_move(source, target)
        with patch.object(update_install, 'move', side_effect=fail_once):
            self.assertFalse(update_install.apply(plan, launch=lambda *args: self.child(0)))
        self.assertEqual((install/'ClearPage.exe').read_bytes(), b'old executable')
        self.assertTrue((install/'_internal/old.dll').exists())

    def test_restart_waits_for_processing_and_recent_activity(self):
        app = SimpleNamespace(LOCK=threading.RLock(), JOBS={}, WORK=dict(active=False, stop=False, data_root=self.root))
        manager = auto_update.UpdateManager(app, install=self.root/'ClearPage.exe')
        manager.set(phase='ready')
        manager.can_restart = True
        manager.heartbeat_at = time.monotonic()
        self.assertFalse(manager.restart())
        manager.last_activity -= 120
        app.WORK['active'] = True
        self.assertFalse(manager.restart(force=True))
        app.WORK['active'] = False
        app.JOBS['a'] = dict(state='processing')
        self.assertFalse(manager.restart(force=True))

    def test_restart_rechecks_state_after_waiting_for_document_lock(self):
        app = SimpleNamespace(JOBS={}, WORK={}, LOCK=None)
        manager = auto_update.UpdateManager(app, install=self.root/'ClearPage.exe')
        class Lock:
            def acquire(self, timeout):
                manager.set(phase='restarting')
                return True
            def release(self):
                pass
        app.LOCK = Lock()
        manager.set(phase='ready')
        with patch.object(auto_update.subprocess, 'Popen') as spawn:
            self.assertFalse(manager.restart(force=True))
            spawn.assert_not_called()

    def test_activity_during_checkpoint_postpones_automatic_restart(self):
        app = SimpleNamespace(LOCK=threading.RLock(), JOBS={'a': dict(state='ready')},
                              WORK=dict(active=False, stop=False, data_root=self.root))
        manager = auto_update.UpdateManager(app, install=self.root/'ClearPage.exe')
        manager.set(phase='ready')
        manager.can_restart = True
        manager.heartbeat_at = time.monotonic()
        manager.last_activity -= 120
        with patch.object(auto_update.saved_work, 'save', side_effect=lambda *args: manager.activity(dict(active=True, can_restart=False))), patch.object(auto_update.subprocess, 'Popen') as spawn:
            self.assertFalse(manager.restart())
            spawn.assert_not_called()
        self.assertEqual(manager.status()['phase'], 'ready')

    def test_main_builds_increment_past_latest_stable(self):
        self.assertEqual(next_version('2.3', None), '2.3')
        self.assertEqual(next_version('2.3', '2.3'), '2.3.1')
        self.assertEqual(next_version('2.3', '2.4.9'), '2.4.10')
        self.assertEqual(next_version('3.0', '2.4.9'), '3.0')

    def test_restart_checkpoints_documents_and_restores_page_choices(self):
        from tests.test_app import fixture
        job_root = self.root/'live'/('a'*24)
        job_root.mkdir(parents=True)
        fixture(job_root/'original.pdf')
        job = dict(id='a'*24, dir=job_root, source=job_root/'original.pdf', name='Synthetic.pdf',
                   count=4, size=100, state='ready', results=[], options={},
                   ui=dict(page=2, keep=[1, 2], reviewed=[2], view='workspace'))
        app = SimpleNamespace(LOCK=threading.RLock(), JOBS={job['id']: job},
                              WORK=dict(active=False, stop=False, data_root=self.root/'profile'))
        manager = auto_update.UpdateManager(app, install=self.root/'ClearPage.exe')
        manager.work = self.root/'helper-work'
        manager.work.mkdir()
        (manager.work/'helper-ready').touch()
        manager.set(phase='ready')
        with patch.object(auto_update.subprocess, 'Popen', return_value=self.child()), patch('clearpage.desktop_shell.close_window') as close:
            self.assertTrue(manager.restart(force=True))
            close.assert_called_once()
        restored = SimpleNamespace(JOBS={}, ROOT=self.root/'restored', WORK=dict(data_root=self.root/'profile'))
        restored.ROOT.mkdir()
        with patch('sys.argv', ['ClearPage.exe', '--resume-update']):
            auto_update.restore_work(restored)
        recovered = restored.JOBS[job['id']]
        self.assertEqual(recovered['ui'], job['ui'])
        self.assertEqual(recovered['source'].read_bytes(), job['source'].read_bytes())
        self.assertTrue(restored.WORK['update_restored'])
