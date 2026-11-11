"""Atomic local workspaces. JSON + PNG/NPY only; never deserialize pickle."""
from pathlib import Path, PurePosixPath
from io import BytesIO
import json
import os
import re
import secrets
import shutil
import time
import zipfile
import numpy as np
from PIL import Image

MAX_BYTES = 2 * 1024**3
FIELDS = ('id', 'name', 'count', 'size', 'options', 'state', 'progress', 'message',
          'results', 'overlays', 'input_type', 'input_note', 'ui', 'repairs',
          'automatic', 'auto_background', 'overlaps', 'repair_color_settings', 'export_keep', 'inversion_model', 'object_selection')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{24}', value):
        raise ValueError('Invalid saved workspace.')
    return value


def save(store, key, jobs):
    identifier(key)
    store.mkdir(parents=True, exist_ok=True)
    if not (store/(key+'.zip')).exists() and len(list(store.glob('*.zip'))) >= 5:
        raise ValueError('Five workspaces are already saved. Delete one saved workspace first.')
    files = [(job, file) for job in jobs for file in job['dir'].rglob('*') if file.is_file()]
    size = sum(file.stat().st_size for _, file in files)
    if size > MAX_BYTES or shutil.disk_usage(store).free < size*2 + 200*1024**2:
        raise ValueError('Not enough space to save this workspace (2 GB maximum). Save PDFs and remove finished files.')
    temporary = store/(key+'-'+secrets.token_hex(6)+'.part')
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            counter = 0
            def encode(value):
                nonlocal counter
                if isinstance(value, (Image.Image, np.ndarray)):
                    counter += 1
                    name = f'state/{counter}.'+('png' if isinstance(value, Image.Image) else 'npy')
                    data = BytesIO()
                    if isinstance(value, Image.Image):
                        value.save(data, format='PNG')
                    else:
                        np.save(data, value, allow_pickle=False)
                    archive.writestr(name, data.getvalue())
                    return {'type': 'image' if isinstance(value, Image.Image) else 'array', 'file': name}
                if isinstance(value, dict):
                    return {'type': 'map', 'items': [[encode(k), encode(v)] for k, v in value.items()]}
                if isinstance(value, tuple):
                    return {'type': 'tuple', 'items': [encode(v) for v in value]}
                if isinstance(value, list):
                    return [encode(v) for v in value]
                if isinstance(value, np.generic):
                    return value.item()
                return value
            for job, file in files:
                if file.is_symlink():
                    raise ValueError('Linked files cannot be saved in a workspace.')
                archive.write(file, 'jobs/'+job['id']+'/'+file.relative_to(job['dir']).as_posix())
            manifest = {'format': 1, 'saved_at': time.time(), 'names': [job['name'] for job in jobs],
                        'jobs': [encode({k: job[k] for k in FIELDS if k in job}) for job in jobs]}
            archive.writestr('workspace.json', json.dumps(manifest, allow_nan=False))
            if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
                raise ValueError('Saved workspace exceeds 2 GB. Remove finished files first.')
        os.replace(temporary, store/(key+'.zip'))
    finally:
        temporary.unlink(missing_ok=True)
    return {'id': key, 'names': manifest['names'], 'saved_at': manifest['saved_at']}


def entries(store):
    found = []
    for file in store.glob('*.zip'):
        try:
            identifier(file.stem)
            with zipfile.ZipFile(file) as archive:
                if archive.getinfo('workspace.json').file_size > 8*1024**2:
                    continue
                data = json.loads(archive.read('workspace.json'))
            found.append({'id': file.stem, 'names': data['names'], 'saved_at': data['saved_at']})
        except (ValueError, KeyError, OSError, zipfile.BadZipFile):
            continue
    return sorted(found, key=lambda item: item['saved_at'], reverse=True)


def load(store, key, root):
    identifier(key)
    staging = root/('restore-'+secrets.token_hex(12))
    staging.mkdir()
    moved = []
    try:
        with zipfile.ZipFile(store/(key+'.zip')) as archive:
            members = archive.infolist()
            if len(members) > 50000 or sum(m.file_size for m in members) > MAX_BYTES:
                raise ValueError('Saved workspace exceeds the size limit.')
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in member.filename or ':' in member.filename:
                    raise ValueError('Invalid path in saved workspace.')
                if not path.parts or path.parts[0] not in ('jobs', 'state', 'workspace.json'):
                    raise ValueError('Unknown file in saved workspace.')
            if archive.getinfo('workspace.json').file_size > 8*1024**2:
                raise ValueError('Saved workspace metadata is too large.')
            manifest = json.loads(archive.read('workspace.json'))
            if manifest.get('format') != 1 or not 0 < len(manifest['jobs']) <= 20:
                raise ValueError('Unsupported saved workspace format.')
            def decode(value):
                if isinstance(value, list):
                    return [decode(v) for v in value]
                if not isinstance(value, dict):
                    return value
                kind = value.get('type')
                if kind == 'map':
                    return {decode(k): decode(v) for k, v in value['items']}
                if kind == 'tuple':
                    return tuple(decode(v) for v in value['items'])
                if kind in ('image', 'array'):
                    name = value['file']
                    if not re.fullmatch(r'state/[0-9]+\.(png|npy)', name):
                        raise ValueError('Invalid saved repair data.')
                    data = BytesIO(archive.read(name))
                    if kind == 'array':
                        return np.load(data, allow_pickle=False)
                    with Image.open(data) as image:
                        return image.convert('RGB').copy()
                raise ValueError('Invalid saved data.')
            jobs = [decode(item) for item in manifest['jobs']]
            ids = [identifier(job['id']) for job in jobs]
            if len(set(ids)) != len(ids) or any((root/key).exists() for key in ids):
                raise ValueError('Workspace conflicts with files already open.')
            for member in members:
                parts = PurePosixPath(member.filename).parts
                if len(parts) < 3 or parts[0] != 'jobs':
                    continue
                if parts[1] not in ids or member.is_dir():
                    raise ValueError('Invalid document in workspace.')
                target = staging.joinpath(*parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
            from clearpage.pdf.engine import inspect_pdf
            for job in jobs:
                source = staging/job['id']/'original.pdf'
                count, _ = inspect_pdf(source)
                if count != job['count']:
                    raise ValueError('Saved document page count changed.')
                if job['state'] not in ('ready', 'review', 'done', 'error', 'cancelled'):
                    job.update(state='ready', results=[], message='Interrupted work restored. Clean this file again.')
                if job['state'] in ('review', 'done'):
                    if len(job['results']) != count or any(not (source.parent/f'{i}-after.png').exists() for i in range(count)):
                        raise ValueError('Saved cleanup is incomplete.')
                destination = root/job['id']
                source.parent.replace(destination)
                moved.append(destination)
                job.update(dir=destination, source=destination/'original.pdf', cancel=False)
            return jobs
    except Exception:
        for folder in moved:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
