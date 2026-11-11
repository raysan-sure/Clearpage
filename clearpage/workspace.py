"""Multi-file workflow, local checkpoints, reusable settings and release checks."""
from pathlib import Path
from io import BytesIO
import json
import os
import re
import secrets
import threading
import urllib.error
import urllib.request
import zipfile
import numpy as np
from PIL import Image
from flask import g, jsonify, request, send_file
from clearpage import saved_work

ACTIVE = ('processing', 'exporting', 'learning', 'restoring')
from clearpage.app_version import VERSION
from clearpage.updates import RELEASES, latest_release


def register(a):
    data_root = Path(os.environ.get('CLEARPAGE_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'ClearPage')))
    a.WORK = {'active': False, 'stop': False, 'kind': None, 'message': '', 'current': None,
              'saved_id': secrets.token_hex(12), 'zip_ready': False, 'data_root': data_root}
    work = a.WORK
    store = lambda: work['data_root']/'saved-work'

    def idle():
        if work['active'] or any(j['state'] in ACTIVE for j in a.JOBS.values()):
            raise ValueError('Wait for the current operation to finish, or stop it first.')

    @a.app.before_request
    def single_worker():
        if request.method == 'POST' and request.path not in ('/api/queue/stop', '/api/quit') and not request.path.startswith('/api/cancel/'):
            a.LOCK.acquire()
            g.workspace_locked=True
            idle()

    @a.app.teardown_request
    def release_request_lock(error):
        if g.pop('workspace_locked',False):
            a.LOCK.release()

    @a.app.get('/api/workspace')
    def state():
        return jsonify(jobs=[a.public(j) for j in a.JOBS.values()],
                       queue={k: work[k] for k in ('active', 'kind', 'message', 'current', 'zip_ready')})

    @a.app.post('/api/view/<identifier>')
    def view(identifier):
        with a.LOCK:
            job = a.job_for(identifier)
            data = request.get_json()
            keep, reviewed = data.get('keep', []), data.get('reviewed', [])
            for values in (keep, reviewed):
                if not isinstance(values, list) or len(values)>job['count'] or any(type(v) is not int or not 0<=v<job['count'] for v in values):
                    raise ValueError('Invalid page selection.')
            page = data.get('page', 0)
            if type(page) is not int or not 0<=page<job['count']:
                raise ValueError('Invalid page number.')
            draft = data.get('options')
            if draft:
                if draft.get('method')=='manual' and not draft.get('strokes'):
                    draft = dict(a.settings(dict(draft, method='preset')), method='manual', strokes=[])
                else:
                    draft = a.settings(draft)
            if data.get('dirty'):
                job.update(state='ready', results=[], message='Settings changed. Clean this file again.')
                keep, reviewed = [], []
            if data.get('confirmed') and any(r.get('changed') and r['page'] not in reviewed for r in job['results']):
                raise ValueError('Review each changed page before finishing this file.')
            previous=job.get('ui',{})
            job['ui'] = dict(page=page, keep=sorted(set(keep)), reviewed=sorted(set(reviewed)),
                             options=draft, confirmed=bool(data.get('confirmed')) and job['state'] in ('review', 'done'))
            if job['state']=='done' and job.get('export_keep') != job['ui']['keep']:
                job['state']='review'
            if any(previous.get(k)!=job['ui'].get(k) for k in ('keep','options','confirmed')):
                work['zip_ready']=False
            return jsonify(a.public(job))

    def reset_review(job):
        job['ui'] = dict(page=0, keep=[r['page'] for r in job['results'] if r.get('changed') and not r.get('uncertain')],
                         reviewed=[], options=job['options'], confirmed=False)

    def run_queue(items, exporting):
        completed, failures = [], 0
        try:
            for identifier, options in items:
                if work['stop']:
                    break
                job = a.job_for(identifier)
                work.update(current=identifier, message=f'{len(completed)+1} of {len(items)}: {job["name"]}')
                if exporting:
                    job.update(state='exporting', cancel=False)
                    a.export_worker(job, job['ui']['keep'])
                    if job['state']=='done':
                        job['export_keep']=list(job['ui']['keep'])
                        completed.append(job)
                    else:
                        failures += 1
                else:
                    with a.LOCK:
                        a.prepare_options(job, options)
                        job.update(state='processing', progress=0, results=[], options=options, cancel=False)
                    a.batch(job, options)
                    reset_review(job)
                    completed.append(job)
                    if job['state'] not in ('review', 'done'):
                        failures += 1
            if exporting and not work['stop'] and not failures and len(completed)==len(items):
                target = a.ROOT/'reviewed-files.zip'
                with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for number, job in enumerate(completed, 1):
                        archive.write(job['dir']/'cleaned.pdf', f'{number:02d}_{Path(job["name"]).stem}_ClearPage-{VERSION}.pdf')
                work['zip_ready']=True
                work['message']=f'{len(completed)} reviewed PDFs ready to download.'
            else:
                work['message']='Queue stopped. Unfinished files can be cleaned again.' if work['stop'] else f'Finished {len(completed)} files. {failures} need another try.' if failures else f'{len(completed)} files ready for individual review.'
        except Exception as error:
            work['message']=str(error) if isinstance(error, ValueError) else 'Queue could not finish. Check the current file and try again.'
        finally:
            work.update(active=False, current=None)

    @a.app.post('/api/queue')
    def queue():
        with a.LOCK:
            idle()
            data = request.get_json()
            ids = data.get('ids', [])
            if not isinstance(ids, list) or not 0<len(ids)<=20 or any(not isinstance(key,str) for key in ids) or len(set(ids))!=len(ids):
                raise ValueError('Choose between 1 and 20 files.')
            exporting = data.get('export') is True
            items=[]
            for key in ids:
                job=a.job_for(key)
                if exporting:
                    if job['state'] not in ('review','done') or not job.get('ui',{}).get('confirmed'):
                        raise ValueError('Finish the review for each selected file before downloading the ZIP.')
                    options=None
                else:
                    options=a.settings(job.get('ui',{}).get('options') or job.get('options') or
                                       dict(method='overlay' if any(r['overlays'] for r in job['overlays']) else 'preset',auto_restore=False))
                    a.prepare_options(job,options)
                items.append((key,options))
            work.update(active=True, stop=False, current=None, zip_ready=False, kind='export' if exporting else 'clean', message='Starting queue...')
            threading.Thread(target=run_queue,args=(items,exporting),daemon=True).start()
            return jsonify(ok=True)

    @a.app.post('/api/queue/stop')
    def stop():
        work['stop']=True
        for job in a.JOBS.values():
            job['cancel']=True
        return jsonify(ok=True)

    @a.app.get('/api/queue/download')
    def download_zip():
        if not work['zip_ready']:
            raise ValueError('Review and export the files first.')
        return send_file(a.ROOT/'reviewed-files.zip',as_attachment=True,download_name='ClearPage-reviewed-files.zip')

    @a.app.get('/api/saved')
    def saved():
        return jsonify(saved_work.entries(store()))

    @a.app.post('/api/save-work')
    def save():
        with a.LOCK:
            idle()
            if not a.JOBS:
                raise ValueError('Open a file first.')
            return jsonify(saved_work.save(store(),work['saved_id'],list(a.JOBS.values())))

    @a.app.post('/api/resume/<key>')
    def resume(key):
        with a.LOCK:
            idle()
            if a.JOBS:
                raise ValueError('Remove the open files before resuming saved work. Save them first if needed.')
            try:
                jobs=saved_work.load(store(),key,a.ROOT)
            except Exception as error:
                raise ValueError('This saved workspace could not be restored. Its saved copy was kept.') from error
            a.JOBS.update({job['id']:job for job in jobs})
            work['saved_id']=key
            return jsonify(ok=True)

    @a.app.post('/api/saved/<key>/delete')
    def delete_saved(key):
        (store()/(saved_work.identifier(key)+'.zip')).unlink(missing_ok=True)
        return jsonify(ok=True)

    @a.app.route('/api/presets',methods=['GET','POST'])
    def presets():
        path=work['data_root']/'cleanup-presets.json'
        items=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if request.method=='POST':
            data=request.get_json()
            name=data.get('name','').strip()
            if not name or len(name)>60 or len(items)>=20:
                raise ValueError('Use a name up to 60 characters. Up to 20 presets can be saved.')
            options=a.settings(data.get('options') or {})
            options['polarity_pages']=[]
            items=[item for item in items if item['name']!=name]+[dict(name=name,options=options)]
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.part')
            temporary.write_text(json.dumps(items),encoding='utf-8');os.replace(temporary,path)
        return jsonify(items)

    @a.app.get('/api/update-status')
    def update_status():
        return jsonify(latest_release())

    @a.app.post('/api/check-update')
    def update():
        return jsonify(latest_release())

    @a.app.get('/api/changes/<identifier>/<int:index>')
    def changes(identifier,index):
        with a.LOCK:
            job=a.job_for(identifier)
            before=np.array(Image.open(a.original(job,index)).convert('RGB'))
            target=a.image_path(job,index,True)
            if not target.exists():
                raise ValueError('Clean this page first.')
            after=np.array(Image.open(target).convert('RGB'))
            changed=np.any(before!=after,axis=2)
            after[changed]=(after[changed]*.45+np.array([240,85,45])*.55).astype('uint8')
            memory=BytesIO();Image.fromarray(after).save(memory,format='PNG');memory.seek(0)
            return send_file(memory,mimetype='image/png')
