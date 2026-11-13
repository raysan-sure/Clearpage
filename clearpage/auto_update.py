"""Verified background Windows updates, with local work saved before restart."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import zipfile

from clearpage.app_version import VERSION
from clearpage import saved_work, updates
from clearpage.update_install import ENTRIES, cleanup_history, write_json

MAX_DOWNLOAD = 512*1024**2
MAX_EXPANDED = 2*1024**3
ACTIVE = ('processing', 'exporting', 'learning', 'restoring')
manager = None


class GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != 'https' or url.hostname not in
                ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
                or url.port not in (None, 443) or url.username or url.password):
            raise ValueError('Update redirected outside GitHub release storage.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(release, destination, progress=lambda value: None, opener=None):
    version = release.get('version')
    updates.parse_version(version)
    expected_url = f'https://github.com/{updates.REPOSITORY}/releases/download/v{version}/ClearPage-{version}-Windows-x64.zip'
    if (release.get('download_url') != expected_url
            or not re.fullmatch(r'[a-f0-9]{64}', release.get('sha256') or '')
            or type(release.get('size')) is not int or not 0 < release['size'] <= MAX_DOWNLOAD):
        raise ValueError('This release does not contain a verifiable automatic update.')
    opener = opener or urllib.request.build_opener(GitHubRedirects()).open
    request = urllib.request.Request(expected_url, headers={'User-Agent': 'ClearPage/'+VERSION})
    digest, total = hashlib.sha256(), 0
    deadline = time.monotonic()+900
    try:
        with opener(request, timeout=30) as response, Path(destination).open('wb') as output:
            while chunk := response.read(1024*1024):
                total += len(chunk)
                if total > release['size'] or time.monotonic() > deadline:
                    raise ValueError('Update download exceeded its expected size or time limit.')
                output.write(chunk)
                digest.update(chunk)
                progress(round(total/release['size']*100))
        if total != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('Update checksum mismatch. Your installed app was not changed.')
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise


def extract(archive, destination, version):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 20000 or sum(m.file_size for m in members) > MAX_EXPANDED:
            raise ValueError('Update archive exceeds its size limit.')
        seen = set()
        reserved = re.compile(r'^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)', re.I)
        for member in members:
            path = PurePosixPath(member.filename)
            parts = path.parts
            key = member.filename.rstrip('/').casefold()
            if (not parts or parts[0] != 'ClearPage' or path.is_absolute()
                    or member.filename.rstrip('/') != path.as_posix()
                    or '..' in parts or '\\' in member.filename or ':' in member.filename
                    or any(p.endswith(('.', ' ')) or reserved.match(p) for p in parts)
                    or stat.S_ISLNK(member.external_attr >> 16) or key in seen
                    or (len(parts) > 1 and parts[1] not in ENTRIES)):
                raise ValueError('Unsafe path in the update ZIP.')
            seen.add(key)
        # Validate every entry before writing any files.
        source.extractall(destination)
    stage = destination/'ClearPage'
    required = ('ClearPage.exe', 'ClearPageUpdater.exe', 'ClearPage.exe.config',
                '_internal/static/ui.js', 'package-manifest.json')
    if any(not (stage/name).is_file() for name in required):
        raise ValueError('Update package is incomplete.')
    manifest = json.loads((stage/'package-manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != 1 or manifest.get('edition') != 'Standard' or manifest.get('version') != version
            or manifest.get('entries') != [name for name in ENTRIES if (stage/name).exists()]):
        raise ValueError('Update package version or contents do not match.')
    return stage


def restore_work(app):
    marker = app.WORK['data_root']/'resume-update.json'
    if '--resume-update' not in sys.argv or not marker.exists():
        return
    try:
        record = json.loads(marker.read_text(encoding='utf-8'))
        jobs = saved_work.load(app.WORK['data_root']/'update-work', record['id'], app.ROOT)
        app.JOBS.update({job['id']: job for job in jobs})
        app.WORK['update_restored'] = True
    except Exception:
        # Keep the checkpoint and marker so startup recovery remains possible.
        app.WORK['update_restore_error'] = True


def healthy_ui():
    if '--update-health-file' not in sys.argv:
        return
    path = Path(sys.argv[sys.argv.index('--update-health-file')+1])
    token = sys.argv[sys.argv.index('--update-health-token')+1]
    if manager and manager.app.WORK.get('update_restore_error'):
        return
    write_json(path, dict(token=token, version=VERSION, ui_ready=True))
    if manager:
        (manager.app.WORK['data_root']/'resume-update.json').unlink(missing_ok=True)


class UpdateManager:
    def __init__(self, app, install=None):
        self.app = app
        self.install = Path(install or sys.executable).resolve().parent
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.check = threading.Event()
        self.last_activity = time.monotonic()
        self.heartbeat_at = 0
        self.can_restart = False
        self.work = None
        self.release = None
        self.state = dict(automatic=True, available=False, current_version=VERSION,
                          phase='idle', message='Automatic updates are enabled.')

    def status(self):
        with self.lock:
            return dict(self.state)

    def set(self, **values):
        with self.lock:
            self.state.update(values)

    def activity(self, data):
        self.heartbeat_at = time.monotonic()
        self.can_restart = data.get('can_restart') is True
        if data.get('active') is True:
            self.last_activity = time.monotonic()

    def prepare(self, release):
        self.set(**{**release, 'automatic': True, 'phase': 'downloading', 'progress': 0,
                    'message': f"Downloading ClearPage {release['version']} in the background…"})
        self.work = Path(tempfile.mkdtemp(prefix='.clearpage-update-', dir=self.install.parent))
        archive = self.work/'package.zip'
        download(release, archive, lambda percent: self.set(progress=percent))
        self.set(phase='verifying', message='Checking the downloaded update…')
        stage = extract(archive, self.work/'new', release['version'])
        # Run the current trusted helper from outside the app folder it replaces.
        shutil.copy2(self.install/'ClearPageUpdater.exe', self.work/'ClearPageUpdater.exe')
        write_json(self.work/'plan.json', dict(install=str(self.install), stage=str(stage),
                   version=release['version'], token=secrets.token_hex(24),
                   failure_file=str(self.app.WORK['data_root']/'update-failure.json')))
        self.set(phase='ready', message='Update ready. ClearPage will save your work and restart when you pause.')

    def restart(self, force=False):
        if self.status()['phase'] != 'ready':
            return False
        if not self.app.LOCK.acquire(timeout=1):
            return False
        try:
            if self.status()['phase'] != 'ready':
                return False
            if (self.app.WORK['active'] or self.app.WORK['stop']
                    or any(job['state'] in ACTIVE or job.get('compression', {}).get('status') == 'processing'
                           for job in self.app.JOBS.values())):
                return False
            if not force and (not self.can_restart or time.monotonic()-self.heartbeat_at > 20
                              or time.monotonic()-self.last_activity < 60):
                return False
            approved_activity = self.last_activity
            if self.app.JOBS:
                key = '0'*24
                saved_work.save(self.app.WORK['data_root']/'update-work', key, list(self.app.JOBS.values()))
                write_json(self.app.WORK['data_root']/'resume-update.json', dict(id=key))
            else:
                (self.app.WORK['data_root']/'resume-update.json').unlink(missing_ok=True)
            if not force and (self.last_activity != approved_activity or not self.can_restart):
                return False
            self.set(phase='restarting', message='Work saved. Installing the update and restarting ClearPage…')
            helper = subprocess.Popen([str(self.work/'ClearPageUpdater.exe'), '--apply', str(self.work/'plan.json'),
                                       '--parent', str(os.getpid())], cwd=self.work,
                                      env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic()+15
            while not (self.work/'helper-ready').exists():
                if helper.poll() is not None or time.monotonic() > deadline:
                    if helper.poll() is None:
                        helper.terminate()
                    raise RuntimeError('The update helper could not start. Your app remains open.')
                time.sleep(.1)
            if not force and (self.last_activity != approved_activity or not self.can_restart):
                helper.terminate()
                helper.wait(timeout=10)
                self.set(phase='ready', message='Update postponed while you work. Your document is still open.')
                return False
            from clearpage.desktop_shell import close_window
            try:
                close_window()
            except Exception:
                helper.terminate()
                helper.wait(timeout=10)
                raise
            return True
        finally:
            self.app.LOCK.release()

    def run(self):
        if self.stop.wait(8):
            return
        next_check = 0
        try:
            cleanup_history(self.install)
        except OSError:
            pass
        while not self.stop.is_set():
            try:
                if self.status()['phase'] == 'restarting':
                    return
                if self.status()['phase'] == 'ready':
                    self.restart()
                elif time.monotonic() >= next_check or self.check.is_set():
                    forced = self.check.is_set()
                    self.check.clear()
                    release = updates.latest_release(force=forced)
                    self.set(**{**release, 'phase': 'idle', 'error': bool(release.get('error'))})
                    next_check = time.monotonic()+(60 if release.get('error') else 900)
                    if release.get('available'):
                        failure_file = self.app.WORK['data_root']/'update-failure.json'
                        if not forced and failure_file.exists():
                            failure = json.loads(failure_file.read_text(encoding='utf-8'))
                            if failure.get('version') == release['version'] and time.time()-failure.get('failed_at', 0) < 86400:
                                self.set(phase='error', message='The previous version was restored after an update failed. Use Check for updates to retry.')
                                continue
                        if release.get('sha256'):
                            self.prepare(release)
                        else:
                            self.set(phase='manual', message='This older release requires a manual download. New releases update automatically.')
            except Exception as error:
                self.set(phase='error', message=str(error), error=True)
                if self.work and not (self.work/'backup').exists():
                    target = self.work.resolve()
                    if target.parent == self.install.parent and target.name.startswith('.clearpage-update-'):
                        shutil.rmtree(target, ignore_errors=True)
                next_check = time.monotonic()+60
            self.stop.wait(3)


def start(app):
    global manager
    if (sys.platform != 'win32' or not getattr(sys, 'frozen', False)
            or any(arg in sys.argv for arg in ('--test-info', '--desktop-smoke-info', '--release-self-test'))
            or os.environ.get('CLEARPAGE_PAYMENT_LOCAL_TEST') == '1'):
        return None
    try:
        package = json.loads((Path(sys.executable).parent/'package-manifest.json').read_text(encoding='utf-8'))
        if package.get('edition') != 'Standard' or package.get('format') != 1:
            return None
    except (OSError, ValueError):
        return None
    restore_work(app)
    manager = UpdateManager(app)
    threading.Thread(target=manager.run, daemon=True).start()
    return manager
