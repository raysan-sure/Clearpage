"""Standalone, reversible Windows package replacement. Only app-owned entries move."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ENTRIES = ('_internal', 'licenses', 'docs', 'README.md', 'START-HERE.txt',
           'DEPENDENCIES.txt', 'THIRD-PARTY-NOTICES.txt', 'ClearPage.exe.config',
           'ClearPageUpdater.exe', 'package-manifest.json', 'ClearPage.exe')


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temporary, path)


def validate_plan(path):
    path = Path(path).resolve(strict=True)
    work = path.parent
    plan = json.loads(path.read_text(encoding='utf-8'))
    install = Path(plan['install']).resolve(strict=True)
    stage = Path(plan['stage']).resolve(strict=True)
    if (not work.name.startswith('.clearpage-update-') or work.parent != install.parent
            or stage != work/'new'/'ClearPage' or work == install
            or not (install/'ClearPage.exe').is_file()):
        raise ValueError('Invalid update installation paths.')
    if not isinstance(plan.get('token'), str) or len(plan['token']) != 48:
        raise ValueError('Invalid update health token.')
    package = json.loads((stage/'package-manifest.json').read_text(encoding='utf-8'))
    if (package.get('format') != 1 or package.get('edition') != 'Standard' or package.get('version') != plan.get('version')
            or package.get('entries') != [name for name in ENTRIES if (stage/name).exists()]):
        raise ValueError('Update package manifest mismatch.')
    for directory in (install, stage):
        if directory.is_symlink() or directory.is_junction():
            raise ValueError('Linked installation directories are not supported.')
        for name in ENTRIES:
            entry = directory/name
            if entry.is_symlink() or entry.is_junction():
                raise ValueError('Linked application entries cannot be replaced.')
    return plan, install, stage, work


def wait_for_parent(pid, ready, timeout=120):
    # Retain a handle to the exact process, rather than polling a reusable PID.
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        raise OSError('Cannot wait for the running ClearPage process.')
    try:
        ready.touch()
        if kernel.WaitForSingleObject(handle, timeout*1000) != 0:
            raise TimeoutError('ClearPage is still open; no files were replaced.')
    finally:
        kernel.CloseHandle(handle)


def move(source, destination):
    deadline = time.monotonic()+20
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.25)


def start_app(install, health=None, token=None):
    args = [str(install/'ClearPage.exe'), '--resume-update']
    if health:
        args += ['--update-health-file', str(health), '--update-health-token', token,
                 '--desktop-test-info', str(health.parent/'desktop.json')]
    return subprocess.Popen(args, cwd=install, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def apply(path, launch=start_app, health_timeout=90):
    plan, install, stage, work = validate_plan(path)
    backup = work/'backup'
    backup.mkdir(exist_ok=False)
    moved_old, moved_new = [], []
    child = None
    health = work/'healthy.json'
    try:
        for name in ENTRIES:
            original, replacement = install/name, stage/name
            if original.exists():
                move(original, backup/name)
                moved_old.append(name)
            if replacement.exists():
                move(replacement, original)
                moved_new.append(name)
            write_json(work/'journal.json', dict(old=moved_old, new=moved_new))
        child = launch(install, health, plan['token'])
        deadline = time.monotonic()+health_timeout
        while time.monotonic() < deadline:
            if health.exists():
                result = json.loads(health.read_text(encoding='utf-8'))
                if result == dict(token=plan['token'], version=plan['version'], ui_ready=True):
                    write_json(work/'result.json', dict(installed=True, version=plan['version'], pid=child.pid))
                    try:
                        failure = plan.get('failure_file')
                        if failure and Path(failure).name == 'update-failure.json':
                            Path(failure).unlink(missing_ok=True)
                        (work/'package.zip').unlink(missing_ok=True)
                    except OSError:
                        pass
                    return True
            if child.poll() is not None:
                break
            time.sleep(.2)
        raise RuntimeError('Updated desktop did not become ready; restoring the previous version.')
    except Exception as error:
        if child is not None and child.poll() is None:
            # WebView2 descendants must exit before moving the DLL directory back.
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                               creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)
            else:
                child.terminate()
            child.wait(timeout=20)
        # Move failed new entries back into the updater's own staging directory.
        for name in reversed(moved_new):
            move(install/name, stage/name)
        for name in reversed(moved_old):
            move(backup/name, install/name)
        if plan.get('failure_file') and Path(plan['failure_file']).name == 'update-failure.json':
            write_json(Path(plan['failure_file']), dict(version=plan['version'], failed_at=time.time()))
        previous = launch(install)
        write_json(work/'result.json', dict(installed=False, rolled_back=True, error=str(error), pid=previous.pid))
        return False


def cleanup_history(install):
    """Retain the newest successful backup; remove only identified updater folders."""
    install = Path(install).resolve()
    completed = []
    for work in install.parent.glob('.clearpage-update-*'):
        if work.is_symlink() or work.is_junction() or not work.is_dir():
            continue
        try:
            plan = json.loads((work/'plan.json').read_text(encoding='utf-8'))
            result = json.loads((work/'result.json').read_text(encoding='utf-8'))
            if Path(plan['install']).resolve() == install and result.get('installed') is True:
                completed.append(work)
        except (OSError, ValueError, KeyError):
            continue
    for work in sorted(completed, key=lambda p: p.stat().st_mtime, reverse=True)[1:]:
        # Resolve and recheck every recursive deletion target against the app parent.
        target = work.resolve()
        if target.parent == install.parent and target != install and target.name.startswith('.clearpage-update-'):
            shutil.rmtree(target)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', required=True, type=Path)
    parser.add_argument('--parent', required=True, type=int)
    parser.add_argument('--health-timeout', type=int, default=90, choices=range(5, 91))
    args = parser.parse_args()
    try:
        _, _, _, work = validate_plan(args.apply)
        wait_for_parent(args.parent, work/'helper-ready')
        apply(args.apply, health_timeout=args.health_timeout)
    except Exception as error:
        write_json(args.apply.parent/'result.json', dict(installed=False, error=str(error)))
        raise
