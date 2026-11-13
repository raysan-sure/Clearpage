"""CI-only tag publication. Validate artifacts first; never overwrite a release."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def numeric(value):
    if not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?', value):
        raise ValueError('Expected a stable v-prefixed version tag.')
    parts=tuple(map(int,value[1:].split('.')))
    return (parts+(0,0,0))[:3]


def validate(folder, tag):
    numeric(tag)
    value=tag[1:]
    archive=folder/f'ClearPage-{value}-Windows-x64.zip'
    report=json.loads((folder/f'ClearPage-{value}-build-report.json').read_text())
    if report.get('version') != value or report.get('preview') is not False or report.get('owner_test') or not report.get('checks'):
        raise ValueError('Only checked production packages may be published.')
    if report.get('sha256') != hashlib.sha256(archive.read_bytes()).hexdigest():
        raise ValueError('Package checksum does not match the verified report.')
    alias=folder/'ClearPage-Windows-x64.zip'
    if not alias.is_file() or hashlib.sha256(alias.read_bytes()).hexdigest() != report['sha256']:
        raise ValueError('The stable website download must match the verified versioned ZIP.')
    expected={'normal','environment-override','changed-config','missing-config'}
    checks=report['checks']
    if {x.get('mode') for x in checks} != expected or any(x.get('locked') is not True for x in checks):
        raise ValueError('Extracted EXE security checks are incomplete.')
    desktop = report.get('desktop_check')
    if (not isinstance(desktop, dict) or desktop.get('renderer') != 'edgechromium'
            or desktop.get('ui_ready') is not True or desktop.get('download_marks') is not True):
        raise ValueError('The downloaded desktop window must pass its launch check.')
    updater = report.get('updater_check')
    if not isinstance(updater, dict) or any(updater.get(key) is not True for key in ('installed', 'rollback', 'resume', 'user_files_preserved')):
        raise ValueError('The bundled updater must pass install, restart, resume and rollback checks.')
    return archive


def publish(folder, tag, repo, existing_release=False, auto_main=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository.')
    archive=validate(folder,tag)
    report=json.loads((folder/f'ClearPage-{tag[1:]}-build-report.json').read_text())
    if auto_main:
        if not re.fullmatch(r'[a-f0-9]{40}', report.get('commit', '')):
            raise ValueError('The automatic release requires the exact built commit.')
        current=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/commits/main'],text=True))
        if current.get('sha') != report['commit']:
            print('A newer main commit is queued; skipping this superseded build.')
            return
    existing=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/releases?per_page=100','--paginate','--slurp'],text=True))
    if existing and isinstance(existing[0],list):
        existing=[item for page in existing for item in page]
    for item in existing:
        if item.get('tag_name') == tag:
            if not existing_release:
                # A tag build may have already published before a release-event run.
                asset=next((a for a in item.get('assets',[]) if a.get('name')==archive.name),{})
                if asset.get('digest') == 'sha256:'+report['sha256']:
                    return
                raise ValueError('This release already exists. It will not be overwritten.')
            if item.get('draft') or item.get('prerelease'):
                raise ValueError('Only a published stable release can receive automatic assets.')
            if item.get('assets'):
                asset=next((a for a in item['assets'] if a.get('name')==archive.name),{})
                if asset.get('digest') == 'sha256:'+report['sha256']:
                    return
                raise ValueError('Published assets cannot be replaced by another build.')
            continue
        if not item.get('draft') and not item.get('prerelease'):
            try: older=numeric(item.get('tag_name',''))
            except ValueError: continue
            if older > numeric(tag): raise ValueError('Refusing to publish an older stable version.')
    if not existing_release:
        target=['--target',report['commit']] if auto_main else ['--verify-tag']
        subprocess.run(['gh','release','create',tag,'--repo',repo,*target,'--draft',
                        '--title','ClearPage '+tag[1:],'--notes-file',str(folder/'release-notes.md')],check=True)
    files=[archive, folder/'ClearPage-Windows-x64.zip', folder/f'ClearPage-{tag[1:]}-build-report.json',folder/f'ClearPage-{tag[1:]}-SHA256.txt']
    subprocess.run(['gh','release','upload',tag,*map(str,files),'--repo',repo],check=True)
    if not existing_release:
        subprocess.run(['gh','release','edit',tag,'--repo',repo,'--draft=false','--latest'],check=True)


if __name__ == '__main__':
    publish(Path('release-assets'),os.environ['RELEASE_TAG'],os.environ['GH_REPO'],
            existing_release=os.environ.get('RELEASE_EXISTING')=='1',
            auto_main=os.environ.get('RELEASE_AUTO_MAIN')=='1')
