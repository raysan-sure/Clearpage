"""Exercise the actual bundled helper, desktop restart, resume and rollback locally."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from clearpage import auto_update, saved_work
from clearpage.update_install import write_json


def stop_tree(pid):
    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                   creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)


def check_updater(archive, previous_archive=None):
    import pikepdf
    with tempfile.TemporaryDirectory(prefix='ClearPage-updater-check-') as folder:
        root = Path(folder)
        with zipfile.ZipFile(previous_archive or archive) as source:
            source.extractall(root)
        install = root/'ClearPage'
        (install/'customer-document.pdf').write_bytes(b'Synthetic user file; must stay untouched.')
        profile = root/'profile'
        fixture = root/'fixture'/('a'*24)
        fixture.mkdir(parents=True)
        with pikepdf.new() as pdf:
            pdf.add_blank_page()
            pdf.save(fixture/'original.pdf')
        job = dict(id='a'*24, name='Synthetic update check.pdf', count=1, size=100,
                   state='ready', results=[], dir=fixture, source=fixture/'original.pdf', options={},
                   ui=dict(page=0, keep=[0], reviewed=[], view='workspace'))
        saved_work.save(profile/'update-work', '0'*24, [job])
        env = {**os.environ, 'CLEARPAGE_DATA_DIR': str(profile), 'LOCALAPPDATA': str(root/'local'),
               'HTTPS_PROXY': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9',
               'NO_PROXY': '127.0.0.1,localhost', 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
        env.pop('CLEARPAGE_PAYMENT_LOCAL_TEST', None)
        results = {}
        for broken in (False, True):
            work = Path(tempfile.mkdtemp(prefix='.clearpage-update-', dir=root))
            with zipfile.ZipFile(archive) as source:
                source.extractall(work/'new')
            stage = work/'new/ClearPage'
            value = json.loads((stage/'package-manifest.json').read_text())['version']
            if broken:
                (stage/'_internal/static/ui.js').write_text('throw new Error("Synthetic failed update");')
            write_json(profile/'resume-update.json', dict(id='0'*24))
            write_json(work/'plan.json', dict(install=str(install), stage=str(stage), version=value,
                       token=secrets.token_hex(24), failure_file=str(profile/'update-failure.json')))
            helper = work/'ClearPageUpdater.exe'
            shutil.copy2(install/'ClearPageUpdater.exe', helper)
            parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'],
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            args = [str(helper), '--apply', str(work/'plan.json'), '--parent', str(parent.pid)]
            if broken:
                args += ['--health-timeout', '10']
            process = subprocess.Popen(args, env=env, cwd=work, creationflags=subprocess.CREATE_NO_WINDOW)
            app_pid = None
            try:
                deadline = time.monotonic()+20
                while not (work/'helper-ready').exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Packaged update helper failed before handoff.')
                    time.sleep(.1)
                parent.terminate()
                parent.wait(timeout=10)
                process.wait(timeout=60)
                result = json.loads((work/'result.json').read_text())
                app_pid = result.get('pid')
                if broken:
                    if result.get('rolled_back') is not True or not (profile/'update-failure.json').exists():
                        raise RuntimeError('Packaged rollback or retry suppression failed.')
                    results['rollback'] = True
                else:
                    if result.get('installed') is not True:
                        raise RuntimeError('Packaged update failed: '+str(result))
                    base = json.loads((work/'desktop.json').read_text())['url']
                    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with client.open(base+'api/session', timeout=10) as response:
                        session = json.load(response)
                    if session.get('name') != job['name'] or session.get('ui') != job['ui']:
                        raise RuntimeError('Updated desktop did not restore the synthetic document and page choices.')
                    results.update(installed=True, resume=True)
                if (install/'customer-document.pdf').read_bytes() != b'Synthetic user file; must stay untouched.':
                    raise RuntimeError('Updater changed an unrelated customer file.')
                results['user_files_preserved'] = True
            finally:
                if parent.poll() is None:
                    parent.terminate(); parent.wait(timeout=10)
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=10)
                if app_pid:
                    stop_tree(app_pid)
        return results
