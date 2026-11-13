"""ClearPage - Windows local browser application."""
from pathlib import Path
import atexit
import gc
import io
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import base64
import subprocess
from functools import wraps
from clearpage.paths import RESOURCE_ROOT
from clearpage.app_version import VERSION
from flask import Flask, request, jsonify, send_file
from werkzeug.serving import make_server, WSGIRequestHandler
from werkzeug.utils import secure_filename
from PIL import Image
import numpy as np
from clearpage.pdf.engine import inspect_pdf, render_page, known_mask, paint_mask, clean, write_pdf, page_fingerprint, save_reviewed_patch, load_reviewed_patch
import cv2
from clearpage.restore import word_repair
from clearpage.restore import auto_restore
from clearpage.restore import scan_inversion
from clearpage.restore import neural_restore
from clearpage.restore import text_restore
from clearpage.pdf import pdf_overlays
from clearpage.pdf import page_polarity
from clearpage.pdf import quality_check
from clearpage.pdf import scan_input
from clearpage.pdf import pdf_selection
from clearpage.presets import dotted_scans
from clearpage.presets import screened_scans
from clearpage.presets import kane_outline
from clearpage.presets import licensed_diagonal
from clearpage.presets import hocking_outline
from clearpage.presets import union_parish
from clearpage.presets import henry_red
from clearpage.presets import lake_outline
from clearpage.presets import cole_preview
from clearpage.presets import not_valid
from clearpage.presets import preble_scans
from clearpage.presets import nassau_scans
from clearpage.presets import scan_patterns
from clearpage.presets import red_scans
from clearpage.presets import cumberland_red
from clearpage.presets import flat_gray_scans
from clearpage.presets import repeated_copy
from clearpage import cleanup_suggestions
from clearpage.presets import huntington
from clearpage.restore import gemini_cleanup
from clearpage.pdf import pdf_compression

BASE = RESOURCE_ROOT
ROOT = Path(tempfile.mkdtemp(prefix='ClearPage-'))
PROFILE_ROOT = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'ClearPage'/'reviewed-pages'
LOCK = threading.RLock()
JOBS = {}
NATIVE_OUTLINE = {method: module for module in (screened_scans, kane_outline, licensed_diagonal, hocking_outline, union_parish, henry_red, lake_outline, cole_preview, not_valid)
                  for method in module.METHODS}
SERVER = None
HOST = None
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 200*1024*1024


@app.before_request
def local_only():
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify(error='ClearPage accepts connections only from this computer.'), 403
    if HOST and request.host != HOST:
        return jsonify(error='This app accepts connections only from its own local window.'), 403
    if request.headers.get('Origin') and request.headers['Origin'] != f'http://{HOST or request.host}':
        return jsonify(error='Cross-site request rejected.'), 403
    if request.headers.get('Sec-Fetch-Site') == 'cross-site' and request.path != '/':
        return jsonify(error='Cross-site request rejected.'), 403
    if request.method == 'POST' and request.headers.get('X-ClearPage') != 'local':
        return jsonify(error='Invalid local request.'), 403


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.errorhandler(413)
def too_large(error):
    return jsonify(error='The file is too large. Maximum file size is 200 MB.'), 413


@app.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


MEMORY_MESSAGE = 'Not enough memory to finish cleanup. Close older ClearPage instances or other large applications, then retry. The original document is unchanged.'


@app.errorhandler(MemoryError)
def memory_failure(error):
    return jsonify(error=MEMORY_MESSAGE), 503


@app.errorhandler(Exception)
def failure(error):
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return jsonify(error=error.description), error.code
    # Do not expose file paths or document content through error traces.
    return jsonify(error='Unable to read or process this file. Try an unlocked PDF or a valid JPG, PNG or TIFF scan.'), 400


def job_for(identifier):
    if identifier not in JOBS:
        raise ValueError('This document session has ended. Open the PDF again.')
    return JOBS[identifier]


def public(job):
    return {key: job[key] for key in ('id', 'name', 'count', 'size', 'options', 'state', 'progress', 'message', 'results', 'overlays', 'input_type', 'input_note', 'ui', 'compression') if key in job}


def require_new_review(job, index=None):
    ui=job.get('ui', {})
    ui['confirmed']=False
    ui['reviewed']=[i for i in ui.get('reviewed',[]) if index is not None and i!=index]
    job['ui']=ui
    WORK['zip_ready']=False


def image_path(job, index, after=False):
    if not 0 <= index < job['count']:
        raise ValueError('Page number is out of range.')
    return job['dir']/f'{index}-{ "after" if after else "before"}.png'


def original(job, index):
    path = image_path(job, index)
    if not path.exists():
        render_page(job['source'], index).save(path)
    return path


def color_signature(options):
    if not options or not options.get('fix_negative'):return (False, ())
    return (True, tuple(options.get('polarity_pages', [])))


def prepare_options(job, options):
    signature = color_signature(options)
    previous = color_signature(job.get('options'))
    if options['method'] in ('preset','manual'):
        has_repairs = any(any(job.get(key, {}).values()) for key in ('repairs','automatic','auto_background'))
        repair_signature = job.get('repair_color_settings', previous)
        if has_repairs and signature != repair_signature:
            raise ValueError('Existing word repairs use different page colors. Keep the previous color setting, or save your PDF and reopen the original before changing it.')
        job['repair_color_settings'] = signature
    if signature != previous:
        job.pop('inversion_model', None)
        job.pop('pending_repair', None)


def working_original(job, index, options=None):
    options = options if options is not None else (job.get('options') or {})
    raw = original(job,index)
    if not options.get('fix_negative'):return raw
    forced = index in options.get('polarity_pages', [])
    key = (index, forced)
    cache = job.setdefault('color_cache', {})
    if key not in cache:
        with Image.open(raw) as source:
            pixels, details = page_polarity.correct(source, forced)
        target=job['dir']/f'{index}-normalized-{int(forced)}.png'
        Image.fromarray(pixels).save(target)
        cache[key]=(target,details)
    return cache[key][0]


def source_ocr_cache(job, index, path):
    # Keep the legacy cache for unmodified source scans; normalized scans are separate.
    return job['dir']/(f'{index}-ocr.json' if path == image_path(job,index) else path.stem+'-ocr.json')


@app.get('/')
def home():
    return send_file(BASE/'static/index.html')


@app.get('/static/<name>')
def static(name):
    if name not in ('ui.js', 'ui.css', 'favicon.svg', 'app.js', 'style.css', 'repair.js', 'workspace.js', 'objects.js', 'sections.js', 'simple.css', 'simple.js',
                    'type-dotted-document.webp', 'type-dotted-map.webp', 'type-unofficial-copy.webp',
                    'type-overlay.webp', 'type-huntington.webp', 'type-repeated-diagonal.webp', 'type-polarity.svg',
                    'type-preble.webp', 'type-not-valid.svg', 'type-red-copy.svg', 'type-flat-gray.svg', 'type-repeated-copy.svg', 'type-nassau.svg', 'type-screened-unofficial.svg', 'type-kane-outline.svg', 'type-licensed-diagonal.svg', 'type-hocking-outline.svg', 'type-union-parish.webp', 'type-henry-red.webp', 'type-lake-outline.webp', 'type-cole-preview.webp'):
        return '', 404
    return send_file(BASE/'static'/name)


@app.post('/api/upload')
def upload():
    with LOCK:
        if len(JOBS) >= 20:
            raise ValueError('Up to 20 files can be open. Remove a finished file before adding another.')
        identifier = secrets.token_hex(12)
        directory = ROOT/identifier
        directory.mkdir()
        source = directory/'original.pdf'
        incoming = directory/'input-upload'
        try:
            total = 0
            with incoming.open('wb') as target:
                while chunk := request.stream.read(1024*1024):
                    total += len(chunk)
                    if total > app.config['MAX_CONTENT_LENGTH']:
                        raise ValueError('Maximum file size is 200 MB.')
                    if total + sum(j['size'] for j in JOBS.values()) > 500*1024*1024:
                        raise ValueError('The open files exceed 500 MB. Remove finished files before adding more.')
                    target.write(chunk)
            with incoming.open('rb') as content:
                is_pdf = content.read(1024).lstrip().startswith(b'%PDF-')
            if is_pdf:
                incoming.replace(source)
                input_info = dict(input_type='PDF', input_note='Original PDF retained.')
            else:
                input_info = scan_input.import_scan(incoming, source)
            count, sizes = inspect_pdf(source)
            from urllib.parse import unquote
            name = secure_filename(unquote(request.headers.get('X-Filename', 'document.pdf')))[:150] or 'document.pdf'
            job = dict(id=identifier, name=name, dir=directory, source=source, count=count, size=total, options=None,
                       state='ready', progress=0, message='Ready to preview.', results=[], cancel=False, **input_info)
            job['overlays'] = pdf_overlays.inspect(source)
            original(job, 0)
            # Charge only after a valid document and its preview have been created.
            BILLING.document(source, consume=True)
            JOBS[identifier] = job
            WORK['zip_ready'] = False
            return jsonify(public(job))
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise


@app.get('/api/job/<identifier>')
def status(identifier):
    return jsonify(public(job_for(identifier)))


@app.get('/api/session')
def session():
    return jsonify(public(next(iter(JOBS.values()))) if JOBS else None)


@app.get('/api/suggestions/<identifier>')
def suggestions(identifier):
    with LOCK:
        job = job_for(identifier)
        if 'suggestions' not in job:
            if job['state'] in ('processing', 'exporting', 'learning', 'restoring'):
                return jsonify(items=[], sampled_pages=[], skipped_pages=[], total_pages=job['count'],
                               note='Cleanup is already running. You can choose another type after it finishes.')
            job['suggestions'] = cleanup_suggestions.suggest(
                job['source'], job['count'], job['overlays'],
                lambda index: original(job, index), BASE/'assets')
        return jsonify(job['suggestions'])


@app.get('/api/gemini')
def gemini_status():
    return jsonify(gemini_cleanup.status())


@app.get('/api/local-ai')
def local_ai_status():
    import importlib.util
    try:
        neural_restore.verify_model(BASE/'models/big-lama.pt')
        if importlib.util.find_spec('torch') is None:
            raise ValueError('The local AI runtime is missing. Start ClearPage with its AI Python environment or use the complete AI Windows package.')
    except ValueError as error:
        return jsonify(ready=False, message=str(error))
    return jsonify(ready=True, message='LaMa model verified; local AI runtime found. No API key or cloud upload is used.')


@app.post('/api/gemini/key')
def gemini_key():
    data = request.get_json()
    gemini_cleanup.configure(data.get('key', ''))
    return jsonify(gemini_cleanup.status())


def gemini_trial_worker(job, index, key):
    """One explicitly selected page; never entered by automatic detection or queues."""
    try:
        with LOCK:
            before = Image.open(original(job, index)).convert('RGB')
        import hashlib
        signature = hashlib.sha256((gemini_cleanup.MODEL + gemini_cleanup.PROMPT + page_fingerprint(before)).encode()).hexdigest()
        cache = job.setdefault('gemini_cache', {})
        if signature in cache:
            path, details = cache[signature]
            with Image.open(path) as image:
                result = image.convert('RGB')
        else:
            job.update(message=f'Sending page {index + 1} to Google Gemini. This may take a few minutes.')
            # No application lock across the network call: status and cancellation remain available.
            result, details = gemini_cleanup.reconstruct(before, key, lambda: job['cancel'])
            path = job['dir']/f'gemini-{signature}.png'
            result.save(path)
            cache[signature] = (path, details)
        if job['cancel']:
            raise InterruptedError()
        with LOCK:
            result.save(image_path(job, index, True))
            changed = int(np.any(np.array(result) != np.array(before), axis=2).sum())
            results = []
            for i in range(job['count']):
                if i == index:
                    results.append(dict(page=i, changed=changed, uncertain=True, ai=True,
                        reconstruction_unverified=True, confidence=None, **details,
                        pattern_note='Gemini AI draft: the whole page may have been reconstructed. Compare all text, numbers, signatures and barcodes with the original. Visual cleanliness does not establish accuracy. Google adds SynthID to generated images.'))
                else:
                    shutil.copyfile(original(job, i), image_path(job, i, True))
                    results.append(dict(page=i, changed=0, uncertain=False))
            job.update(results=results, state='review', progress=job['count'],
                       message=f'Gemini draft ready for page {index + 1}. Other pages retain their originals. Review before exporting.')
    except InterruptedError:
        job.update(state='cancelled', message='Trial stopped. The original is unchanged. A submitted Google request may still be billed.')
    except ValueError as error:
        job.update(state='error', message=str(error))
    except Exception:
        job.update(state='error', message='Gemini trial failed. No draft was applied; the original is unchanged.')


@app.post('/api/gemini/trial/<identifier>')
def gemini_trial(identifier):
    with LOCK:
        job = job_for(identifier)
        if job['state'] in ('processing', 'exporting', 'learning', 'restoring'):
            raise ValueError('Wait for the current operation to finish.')
        data = request.get_json()
        index = data.get('page')
        if type(index) is not int or not 0 <= index < job['count']:
            raise ValueError('Choose a valid page for the Gemini trial.')
        if data.get('consent') is not True:
            raise ValueError('Confirm sending this page to Google Gemini first.')
        key = gemini_cleanup.credential()
        job.update(state='processing', progress=0, results=[], options={'method': 'gemini_draft'},
                   cancel=False, message='Preparing the Gemini page trial...', ui={})
        WORK['zip_ready'] = False
        threading.Thread(target=gemini_trial_worker, args=(job, index, key), daemon=True).start()
        return jsonify(public(job))


@app.get('/api/thumbnail/<identifier>/<int:index>')
def page_thumbnail(identifier, index):
    with LOCK:
        job = job_for(identifier)
        image_path(job, index)  # Validate the page before resolving a cache path.
        path = job['dir']/f'{index}-thumbnail.jpg'
        if not path.exists():
            with Image.open(original(job, index)) as source:
                preview = source.convert('RGB')
                preview.thumbnail((180, 240), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                preview.save(output, format='JPEG', quality=80)
                path.write_bytes(output.getvalue())
        return send_file(path, mimetype='image/jpeg')


@app.get('/api/page/<identifier>/<int:index>/<kind>')
def page(identifier, index, kind):
    with LOCK:
        job = job_for(identifier)
        if kind not in ('before', 'after', 'mask'):
            raise ValueError('Invalid preview type.')
        path = original(job, index)
        if kind == 'after':
            path = image_path(job, index, True)
            if not path.exists():
                path = original(job, index)
        elif kind == 'mask':
            path = job['dir']/f'{index}-mask.png'
            if not path.exists():
                raise ValueError('Generate a preview first.')
        if kind != 'mask' and (job.get('options') or {}).get('method') in cole_preview.METHODS:
            cleaned = kind == 'after' and path == image_path(job, index, True)
            source = job['dir']/f'{index}-{cole_preview.CACHE}.pdf' if cleaned else job['source']
            if source.is_file():
                detail = job['dir']/f'{index}-{cole_preview.CACHE}-{kind}-display.png'
                if not detail.exists() or detail.stat().st_mtime_ns < source.stat().st_mtime_ns:
                    image = cole_preview.display_image(source, 0 if cleaned else index)
                    if image is not None:
                        image.save(detail)
                    else:
                        detail.unlink(missing_ok=True)
                if detail.is_file():
                    path = detail
        return send_file(path, mimetype='image/png')


def settings(data):
    method = data.get('method', 'preset')
    pages = data.get('polarity_pages', [])
    if not isinstance(pages, list) or len(pages)>300 or any(type(i) is not int or not 0<=i<300 for i in pages):
        raise ValueError('Invalid manual inversion page selection.')
    color = dict(fix_negative=bool(data.get('fix_negative', False)), polarity_pages=sorted(set(pages)))
    if method == 'huntington_background':
        return dict(method=method, mode='careful', strokes=[], use_reviewed=False,
                    auto_restore=bool(data.get('auto_restore', False)), scan_inversion=False,
                    fix_negative=False, polarity_pages=[])
    if method in (*dotted_scans.METHODS, *NATIVE_OUTLINE, *preble_scans.METHODS, *nassau_scans.METHODS, *scan_patterns.METHODS):
        return dict(method=method, mode='lama' if method == 'repeated_diagonal_ai' else 'careful', strokes=[], use_reviewed=False,
                    auto_restore=False, scan_inversion=False, fix_negative=False, polarity_pages=[])
    if method == 'polarity':
        return dict(method='polarity', mode='careful', strokes=[], use_reviewed=False,
                    auto_restore=False, scan_inversion=False, polarity_pages=sorted(set(pages)))
    if method in ('overlay', 'object'):
        return dict(method=method, mode='careful', strokes=[], use_reviewed=False,
                    auto_restore=False, scan_inversion=False, **color)
    mode = data.get('mode', 'careful')
    if method not in ('preset', 'manual') or mode not in ('careful', 'light', 'strong', 'lama'):
        raise ValueError('Invalid cleanup settings.')
    if mode == 'lama' and data.get('scan_inversion'):
        raise ValueError('Choose LaMa or PureScan for this pass; they use different restoration methods.')
    strokes = data.get('strokes', [])
    if data.get('scan_inversion') and method != 'preset':
        raise ValueError('PureScan alignment currently supports the Huntington preset. Disable it for a custom painted area.')
    if method == 'manual':
        test = paint_mask((100, 100), strokes)
        if not test.any():
            raise ValueError('Paint over the watermark on the original page first.')
    return dict(method=method, mode=mode, strokes=strokes, use_reviewed=bool(data.get('use_reviewed', True)),
                auto_restore=bool(data.get('auto_restore',False)), scan_inversion=bool(data.get('scan_inversion',False)), **color)


def cleanup_page(job, index, options):
    before = working_original(job, index, options)
    image = Image.open(before).convert('RGB')
    reviewed = load_reviewed_patch(image, PROFILE_ROOT) if options.get('use_reviewed', True) and options['mode']!='lama' else None
    if reviewed is not None:
        mask = np.any(reviewed != np.array(image), axis=2)
        Image.fromarray(reviewed).save(image_path(job, index, True))
        overlay = np.array(image)
        overlay[mask] = (overlay[mask]*.45 + np.array([240, 85, 45])*.55).astype('uint8')
        Image.fromarray(overlay).save(job['dir']/f'{index}-mask.png')
        return dict(page=index, confidence=1., matched=True, changed=int(mask.sum()), protected=0, reviewed=True, uncertain=False)
    if options['method'] == 'preset':
        mask, confidence = known_mask(image, BASE/'assets/huntington.npy', min_score=.35 if options['mode']=='lama' else .18)
    else:
        mask, confidence = paint_mask(image.size, options['strokes']), None
    if options['mode']=='lama':
        # Cache raw AI output before word repairs. Undo/reprocessing cannot bake
        # confirmed words into a supposedly fresh prediction or rerun the model.
        import hashlib
        key=hashlib.sha256(mask.tobytes()+page_fingerprint(image).encode()).hexdigest()[:24]
        cache=job['dir']/f'{index}-lama-{key}.png'
        if cache.exists():result=np.array(Image.open(cache).convert('RGB'))
        else:
            result=neural_restore.inpaint(np.array(image),mask,BASE/'models/big-lama.pt',
                cancelled=lambda:job['cancel'],progress=lambda n,total:job.update(message=f'LaMa: page {index+1} of {job["count"]}, region {n} of {total}.'))
            Image.fromarray(result).save(cache)
        stats=dict(changed=int(np.any(result!=np.array(image),axis=2).sum()),protected=0,ai=True)
    else:
        result, stats = clean(image, mask, options['mode'])
    Image.fromarray(result).save(image_path(job, index, True))
    preview = np.array(image)
    preview[mask] = (preview[mask]*.45 + np.array([240, 85, 45])*.55).astype('uint8')
    Image.fromarray(preview).save(job['dir']/f'{index}-mask.png')
    return dict(page=index, confidence=round(confidence, 3) if confidence is not None else None,
                matched=bool(mask.any()), reviewed=False, uncertain=(options['mode']=='lama' and bool(mask.any())) or (confidence is not None and .18 <= confidence < .35) or bool(stats.get('scan_overlap_regions')), **stats)


def process_page(job, index, options):
    if options['method'] in NATIVE_OUTLINE:
        module = NATIVE_OUTLINE[options['method']]
        display = getattr(module, 'display_image', lambda *_: None)
        before = display(job['source'], index)
        if before is None:
            before = Image.open(original(job, index)).convert('RGB')
        else:
            before = before.convert('RGB')
            before.save(image_path(job, index))
        target, details = module.prepare(job['source'], index, job['dir'], lambda: job['cancel'])
        after = display(target, 0)
        if after is None:
            after = render_page(target, 0)
        else:
            after = after.convert('RGB')
        after.save(image_path(job, index, True))
        overlay = np.array(before)
        changed = np.any(overlay != np.array(after), axis=2)
        overlay[changed] = (overlay[changed]*.45 + np.array([240, 85, 45])*.55).astype('uint8')
        Image.fromarray(overlay).save(job['dir']/f'{index}-mask.png')
        job.pop('pending_repair', None)
        return dict(page=index, **details)
    if options['method'] in nassau_scans.METHODS:
        before = Image.open(original(job, index)).convert('RGB')
        target, details = nassau_scans.prepare(job['source'], index, job['dir'], cancelled=lambda: job['cancel'])
        after = render_page(target, 0)
        after.save(image_path(job, index, True))
        overlay = np.array(before)
        changed = np.any(overlay != np.array(after), axis=2)
        overlay[changed] = (overlay[changed]*.45 + np.array([240, 85, 45])*.55).astype('uint8')
        Image.fromarray(overlay).save(job['dir']/f'{index}-mask.png')
        return dict(page=index, **details)
    if options['method'] in preble_scans.METHODS:
        before = Image.open(original(job, index)).convert('RGB')
        target, details = preble_scans.prepare(job['source'], index, job['dir'], BASE/'assets', lambda: job['cancel'])
        after = render_page(target, 0)
        after.save(image_path(job, index, True))
        overlay = np.array(before)
        changed = np.any(overlay != np.array(after), axis=2)
        overlay[changed] = (overlay[changed]*.45 + np.array([240, 85, 45])*.55).astype('uint8')
        Image.fromarray(overlay).save(job['dir']/f'{index}-mask.png')
        return dict(page=index, **details)
    if options['method'] in scan_patterns.METHODS:
        before = Image.open(original(job, index)).convert('RGB')
        cancelled = lambda: job['cancel']
        if options['method'] == 'huntington_background':
            try:
                path = original(job, index)
                ocr = local_ocr(path, source_ocr_cache(job, index, path))
                ocr_note = ''
            except ValueError:
                ocr = None
                ocr_note = ' Text-line reading unavailable; background-only pass retained.'
            pixels, changed, details = scan_patterns.huntington_background(before, BASE/'assets', cancelled, ocr)
            image = Image.fromarray(pixels)
            for repair in job.get('automatic', {}).get(index, []) + job.get('repairs', {}).get(index, []):
                image.paste(repair['patch'], repair['box'])
            pixels = np.array(image)
            changed = np.any(pixels != np.array(before), axis=2)
            details.update(changed=int(changed.sum()), repairs=len(job.get('repairs', {}).get(index, [])),
                           automatic=len(job.get('automatic', {}).get(index, [])),
                           overlaps=[r for r in job.get('overlaps', {}).get(index, []) if not any(
                               p['box'][0] <= (r['box'][0]+r['box'][2])/2 <= p['box'][2]
                               and p['box'][1] <= (r['box'][1]+r['box'][3])/2 <= p['box'][3]
                               for p in job.get('repairs', {}).get(index, [])+job.get('automatic', {}).get(index, []))])
            details['matched'] = details['matched'] or bool(changed.any())
            details['uncertain'] = bool(changed.any())
            details['pattern_note'] += ocr_note
        elif options['method'] == 'repeated_copy':
            native = repeated_copy.prepare(job['source'], index, job['dir'], cancelled)
            if native is not None:
                target, details = native
                pixels = np.array(render_page(target, 0))
                changed = np.any(pixels != np.array(before), axis=2)
            else:
                pixels = np.array(before)
                changed = np.zeros(pixels.shape[:2], bool)
                details = repeated_copy.unchanged_details()
        elif options['method'] == 'flat_gray_copy':
            native = flat_gray_scans.prepare(job['source'], index, job['dir'], cancelled)
            if native is not None:
                target, details = native
                pixels = np.array(render_page(target, 0))
                changed = np.any(pixels != np.array(before), axis=2)
            else:
                pixels = np.array(before)
                changed = np.zeros(pixels.shape[:2], bool)
                details = flat_gray_scans.unchanged_details('Page kept unchanged: this preset needs one supported RGB scan image. It does not threshold rendered text or ordinary grayscale pages.')
        elif options['method'] in ('red_copy', 'cumberland_red'):
            cumberland = options['method'] == 'cumberland_red'
            native = red_scans.prepare(job['source'], index, job['dir'], cancelled, cumberland=cumberland)
            if native is not None:
                target, details = native
                pixels = np.array(render_page(target, 0))
                changed = np.any(pixels != np.array(before), axis=2)
            else:
                clean_red = cumberland_red.clean if cumberland else red_scans.clean
                pixels, changed, details = clean_red(before, cancelled)
        elif options['method'] == 'unofficial_copy':
            from clearpage.presets import gray_scans
            native = gray_scans.prepare(job['source'], index, job['dir'], BASE/'assets', cancelled)
            if native is not None:
                target, details = native
                pixels = np.array(render_page(target, 0))
                changed = np.any(pixels != np.array(before), axis=2)
            else:
                pixels, changed, details = scan_patterns.unofficial_copy(before, BASE/'assets', cancelled)
        elif options['method'] == 'repeated_diagonal_ai':
            selected, retained, note = scan_patterns.repeated_ai_plan(before, cancelled)
            source = np.array(before)
            pixels = source.copy()
            if selected.any():
                import hashlib
                key = hashlib.sha256(b'repeated-ai-v4-source-edges'+selected.tobytes()+retained.tobytes()+page_fingerprint(before).encode()).hexdigest()[:24]
                cache = job['dir']/f'{index}-repeated-ai-{key}.png'
                if cache.exists():
                    with Image.open(cache) as cached:
                        pixels = np.array(cached.convert('RGB'))
                else:
                    pixels = neural_restore.inpaint(source, selected, BASE/'models/big-lama.pt',
                        cancelled=cancelled, progress=lambda n,total:job.update(message=f'AI draft: page {index+1} of {job["count"]}, region {n} of {total}.'))
                    if cancelled(): raise InterruptedError('Cancelled')
                    if pixels.shape != source.shape or pixels.dtype != np.uint8 or not np.array_equal(pixels[~selected], source[~selected]):
                        raise ValueError('AI output failed pixel-preservation checks. No draft was applied.')
                    pixels[retained] = source[retained]
                    Image.fromarray(pixels).save(cache)
            if cancelled(): raise InterruptedError('Cancelled')
            if pixels.shape != source.shape or pixels.dtype != np.uint8 or not np.array_equal(pixels[~selected], source[~selected]):
                raise ValueError('AI output failed pixel-preservation checks. No draft was applied.')
            if not np.array_equal(pixels[retained], source[retained]):
                raise ValueError('AI draft failed original-stroke checks. No draft was applied.')
            changed = np.any(pixels != source, axis=2)
            details = dict(scan_pattern=True, ai=True, confidence=None, matched=bool(selected.any()),
                mask_strategy='full_coverage_source_restore_v4', retained_source_pixels=int(retained.sum()),
                changed=int(changed.sum()), protected=int((~selected).sum()), selected_pixels=int(selected.sum()),
                reviewed=False, uncertain=bool(selected.any()), reconstruction_unverified=bool(selected.any()), pattern_note=note)
        elif options['method'] == 'repeated_document':
            pixels, changed, details = scan_patterns.repeated_document(before, cancelled)
        else:
            pixels, changed, details = scan_patterns.repeated_diagonal(before, cancelled)
        Image.fromarray(pixels).save(image_path(job, index, True))
        overlay = np.array(before)
        # Bound the float64 preview temporary instead of selecting an entire
        # rendered page (potentially hundreds of MB) in one allocation.
        for row in range(0, overlay.shape[0], 128):
            block = overlay[row:row+128]
            mask = changed[row:row+128]
            block[mask] = (block[mask]*.45 + np.array([240,85,45])*.55).astype('uint8')
        Image.fromarray(overlay).save(job['dir']/f'{index}-mask.png')
        job.pop('pending_repair', None)
        return dict(page=index, **details)
    if options['method'] in dotted_scans.METHODS:
        before = Image.open(original(job, index)).convert('RGB')
        target, details = dotted_scans.prepare(job['source'], index, options['method'], job['dir'], before, lambda: job['cancel'])
        after = render_page(target, 0)
        after.save(image_path(job, index, True))
        overlay = np.array(before)
        changed = np.any(np.array(after) != overlay, axis=2)
        overlay[changed] = (overlay[changed]*.45 + np.array([240,85,45])*.55).astype('uint8')
        Image.fromarray(overlay).save(job['dir']/f'{index}-mask.png')
        job.pop('pending_repair', None)
        return dict(page=index, **details)
    if options['method'] == 'polarity':
        source = Image.open(original(job, index)).convert('RGB')
        pixels, details = page_polarity.correct(source, index in options.get('polarity_pages', []))
        Image.fromarray(pixels).save(image_path(job, index, True))
        source.save(job['dir']/f'{index}-mask.png')
        job.pop('pending_repair', None)
        return dict(page=index, confidence=None, matched=details['applied'],
                    changed=int(np.any(pixels != np.asarray(source), axis=2).sum()),
                    protected=0, reviewed=False, uncertain=details['applied'], polarity=details)
    if options['method'] in ('overlay', 'object'):
        before = np.array(Image.open(original(job, index)).convert('RGB'))
        selected = options['method'] == 'object'
        if selected and not job.get('object_selection'):
            raise ValueError('Find text objects and choose a watermark first.')
        target = job['dir']/('selected-preview.pdf' if selected else 'overlay-preview.pdf')
        if not target.exists():
            if selected:
                pdf_selection.write_selected(job['source'], target, range(job['count']), job['object_selection'], lambda:job['cancel'])
            else:
                pdf_overlays.write_clean(job['source'], target, range(job['count']), lambda: job['cancel'])
        image = render_page(target, index)
        image.save(job['dir']/f'{index}-structural.png')
        color = None
        if options.get('fix_negative'):
            pixels, color = page_polarity.correct(image, index in options.get('polarity_pages', []))
            image = Image.fromarray(pixels)
        image.save(image_path(job, index, True))
        changed = np.any(np.array(image) != before, axis=2)
        before[changed] = (before[changed]*.45 + np.array([240,85,45])*.55).astype('uint8')
        Image.fromarray(before).save(job['dir']/f'{index}-mask.png')
        info = (dict(overlays=len(job['object_selection'].get(str(index),[])), providers=['Selected text object'])
                if selected else job['overlays'][index])
        return dict(page=index, confidence=None, matched=bool(info['overlays']) or bool(color and color['applied']), changed=int(changed.sum()),
                    protected=0, reviewed=False, uncertain=bool(color and color['applied']) or (selected and bool(changed.any())), structural=True, color_correction=color, **info)
    result = cleanup_page(job, index, options)
    if options.get('fix_negative'):
        color = job['color_cache'][(index, index in options.get('polarity_pages', []))][1]
        result['color_correction'] = color
        if color['applied']:
            result['matched'] = True
            result['uncertain'] = True
    path = image_path(job, index, True)
    image = Image.open(path).convert('RGB')
    if options.get('scan_inversion'):
        pixels, details = scan_inversion.apply(Image.open(working_original(job,index,options)).convert('RGB'), np.array(image),
                                               job['inversion_model'], BASE/'assets/huntington.npy')
        image=Image.fromarray(pixels)
        result.update(details)
    for correction in job.get('auto_background',{}).get(index,[]):
        pixels=np.array(image)
        pixels.reshape(-1,3)[correction['indices']]=correction['pixels']
        image=Image.fromarray(pixels)
    for repair in job.get('automatic',{}).get(index,[]):
        image.paste(repair['patch'],repair['box'])
    for repair in job.get('repairs', {}).get(index, []):
        image.paste(repair['patch'], repair['box'])
    image.save(path)
    result['repairs'] = len(job.get('repairs', {}).get(index, []))
    result['automatic'] = len(job.get('automatic',{}).get(index,[]))
    result['fitted_words'] = sum(r.get('method')=='font-fit' for r in job.get('automatic',{}).get(index,[]))
    result['background_pixels'] = sum(len(p['indices']) for p in job.get('auto_background',{}).get(index,[]))
    result['overlaps'] = job.get('overlaps',{}).get(index,[])
    result['changed'] = int(np.any(np.array(image) != np.array(Image.open(original(job,index)).convert('RGB')),axis=2).sum())
    job.pop('pending_repair', None)
    return result


def repair_job(identifier, data):
    job = job_for(identifier)
    if (job.get('options') or {}).get('method') in ('gemini_draft', 'overlay', 'object', 'polarity', *dotted_scans.METHODS, *NATIVE_OUTLINE, *preble_scans.METHODS, *nassau_scans.METHODS, *(m for m in scan_patterns.METHODS if m != 'huntington_background')):
        raise ValueError('Switch to image cleanup to repair words; overlay removal and black-page correction do not apply word repairs.')
    if job['state'] not in ('review', 'done'):
        raise ValueError('Process all pages first, then repair words in the cleaned preview.')
    index = data.get('page')
    if type(index) is not int or not 0 <= index < job['count']:
        raise ValueError('Invalid repair page.')
    return job, index


def preview_data(image):
    memory = io.BytesIO()
    image.save(memory, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(memory.getvalue()).decode('ascii')


def local_ocr(path,cache):
    if not cache.exists():
        try:
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(BASE/'assets/ocr-word.ps1'),
                            '-InputPath',str(path),'-OutputPath',str(cache)],check=True,timeout=45,
                           capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.SubprocessError):
            cache.unlink(missing_ok=True)
            raise ValueError('Windows OCR is unavailable. Manual word repair is still available; no automatic repairs were applied.')
    return json.loads(cache.read_text(encoding='utf-8-sig'))


def restoration_worker(job,clear=False):
    """Stage the entire analysis before committing, so cancellation is atomic."""
    previous={i:list(v) for i,v in job.get('automatic',{}).items()}
    staged={i:list(v) for i,v in previous.items()}
    background={i:list(v) for i,v in job.get('auto_background',{}).items()}
    overlaps={}
    try:
        if clear:
            staged={}
            background={}
        else:
            source_ocr={};clean_ocr={};masks={}
            def donor_pages():
                for index in range(job['count']):
                    if job['cancel']:raise InterruptedError()
                    with LOCK:
                        path=working_original(job,index)
                        image=Image.open(path).convert('RGB')
                        if job['options']['method'] in ('preset', 'huntington_background'):
                            mask,confidence=known_mask(image,BASE/'assets/huntington.npy',min_score=.35)
                        else:mask=paint_mask(image.size,job['options']['strokes'])
                        mask_path=job['dir']/f'{index}-restore-mask.npy'
                        np.save(mask_path,mask,allow_pickle=False);masks[index]=mask_path
                    job.update(progress=index+1,message=f'Reading clean word samples: page {index+1} of {job["count"]}.')
                    source_ocr[index]=local_ocr(path,source_ocr_cache(job,index,path))
                    yield index,cv2.cvtColor(np.array(image),cv2.COLOR_RGB2GRAY),mask,source_ocr[index]
            index_words=auto_restore.make_index(donor_pages())
            total=0
            for index in range(job['count']):
                if job['cancel']:raise InterruptedError()
                job.update(progress=index+1,message=f'Reconstructing matching words: page {index+1} of {job["count"]}; {total} new matches.')
                with LOCK:
                    source=Image.open(working_original(job,index)).convert('RGB')
                    current=Image.open(image_path(job,index,True)).convert('RGB')
                    mask=np.load(masks[index],allow_pickle=False)
                if not mask.any():overlaps[index]=[];continue
                fingerprint=page_fingerprint(current)[:16]
                clean_ocr[index]=local_ocr(image_path(job,index,True),job['dir']/f'{index}-{fingerprint}-ocr.json')
                if job['options']['method'] == 'huntington_background':
                    locked = np.zeros(mask.shape, bool)
                    for repair in job.get('repairs', {}).get(index, []) + previous.get(index, []):
                        x,y,xx,yy = repair['box']; locked[y:yy,x:xx] = True
                    _, candidates = huntington.exact_donors(np.array(source), np.array(current), mask,
                        source_ocr[index], clean_ocr[index], index_words, locked, lambda: job['cancel'])
                    for candidate in candidates:
                        candidate = dict(candidate)
                        candidate['patch'] = Image.fromarray(candidate['patch'])
                        staged.setdefault(index, []).append(candidate)
                        total += 1
                    # Keep all uncertain readings together. OCR is a review hint,
                    # never authority to invent a digit or a fully hidden letter.
                    occupied = locked.copy()
                    for candidate in candidates:
                        x,y,xx,yy = candidate['box']; occupied[y:yy,x:xx] = True
                    overlaps[index] = [dict(text=w['text'], box=auto_restore.word_box(w))
                        for w in auto_restore.words(clean_ocr[index], mask.shape)
                        if mask[w['y']:w['y']+w['h'],w['x']:w['x']+w['w']].any()
                        and not occupied[w['y']:w['y']+w['h'],w['x']:w['x']+w['w']].any()]
                    continue
                correction=auto_restore.background_patch(np.array(current),mask,clean_ocr[index])
                if len(correction['indices']):background.setdefault(index,[]).append(correction)
                candidates,remaining=auto_restore.proposals(cv2.cvtColor(np.array(source),cv2.COLOR_RGB2GRAY),
                    cv2.cvtColor(np.array(current),cv2.COLOR_RGB2GRAY),mask,source_ocr[index],clean_ocr[index],index_words)
                protected=job.get('repairs',{}).get(index,[])+previous.get(index,[])
                for candidate in candidates:
                    candidate=dict(candidate)
                    x,y,xx,yy=candidate['box']
                    if any(max(x,r['box'][0])<min(xx,r['box'][2]) and max(y,r['box'][1])<min(yy,r['box'][3]) for r in protected):continue
                    if candidate.get('needs_confirmation'):
                        remaining.insert(0,dict(text=candidate['ocr_text'],box=candidate['box'],suggested_text=candidate['text'],
                                                donor_page=candidate['donor_page'],donor_rect=candidate['donor_rect']))
                        continue
                    patch=Image.fromarray(candidate.pop('gray')).convert('RGB')
                    staged.setdefault(index,[]).append(dict(candidate,patch=patch,method='automatic'))
                    total+=1
                fitted,suggestions=text_restore.proposals(np.array(source),np.array(current),mask,
                    source_ocr[index],clean_ocr[index],BASE/'models'/text_restore.MODEL_NAME,
                    protected=job.get('repairs',{}).get(index,[])+staged.get(index,[]),cancelled=lambda:job['cancel'],
                    vocabulary=index_words)
                for candidate in fitted:
                    patch=Image.fromarray(candidate.pop('gray')).convert('RGB')
                    staged.setdefault(index,[]).append(dict(candidate,patch=patch))
                    total+=1
                boxes=[r['box'] for r in staged.get(index,[])]
                remaining=[r for r in remaining if not any(max(r['box'][0],b[0])<min(r['box'][2],b[2]) and
                    max(r['box'][1],b[1])<min(r['box'][3],b[3]) for b in boxes)]
                remaining=suggestions+remaining
                overlaps[index]=remaining
        if job['cancel']:raise InterruptedError()
        with LOCK:
            job['automatic']=staged;job['overlaps']=overlaps;job['auto_background']=background
            for index in range(job['count']):
                result=process_page(job,index,job['options'])
                job['results'][index]=result
            count=sum(len(v) for v in staged.values())
            summary = (f'Restored {count} matching scanned words. Numbers and uncertain spellings remain for confirmation.'
                       if job['options']['method']=='huntington_background' else
                       f'Restored {count} words using scanned matches and source-matched font fits.')
            job.update(state='review',progress=job['count'],message=('Automatic word repairs removed. Manual repairs are retained.' if clear else
                summary+' Review the listed overlap areas before saving.'))
    except InterruptedError:
        job.update(state='review',message='Automatic restoration stopped. No new word repairs were applied.')
    except ValueError as error:
        job.update(state='review',message=str(error))
    except Exception:
        job.update(state='review',message='Automatic restoration could not finish. Review the existing cleanup or use manual word repair.')


@app.post('/api/restore/<identifier>')
def restore_all_words(identifier):
    with LOCK:
        job=job_for(identifier)
        if (job.get('options') or {}).get('method') in ('gemini_draft', 'overlay', 'object', 'polarity', *dotted_scans.METHODS, *NATIVE_OUTLINE, *preble_scans.METHODS, *nassau_scans.METHODS, *(m for m in scan_patterns.METHODS if m != 'huntington_background')):
            raise ValueError('Word restoration is disabled for overlay removal and black-page correction.')
        if job['state'] not in ('review','done'):
            raise ValueError('Process all pages before restoring words.')
        job.update(state='restoring',cancel=False,progress=0,message='Preparing automatic word restoration...')
        require_new_review(job)
        clear=request.get_json().get('clear') is True
        threading.Thread(target=restoration_worker,args=(job,clear),daemon=True).start()
        return jsonify(public(job))


@app.post('/api/repair/<identifier>/read')
def word_read(identifier):
    with LOCK:
        data=request.get_json()
        job,index=repair_job(identifier,data)
        current=Image.open(image_path(job,index,True)).convert('RGB')
        box=word_repair.rectangle(data.get('rect'),current.size)
        source=Image.open(working_original(job,index)).convert('RGB')
        model=BASE/'models'/text_restore.MODEL_NAME
        return jsonify(original=text_restore.recognize(np.array(source.crop(box)),model),
                       cleaned=text_restore.recognize(np.array(current.crop(box)),model))


@app.post('/api/repair/<identifier>/preview')
def word_preview(identifier):
    with LOCK:
        data = request.get_json()
        job, index = repair_job(identifier, data)
        current = Image.open(image_path(job,index,True)).convert('RGB')
        box = word_repair.rectangle(data.get('rect'),current.size)
        donor = None
        if data.get('method') == 'donor':
            donor_page = data.get('donor_page', index)
            if type(donor_page) is not int or not 0 <= donor_page < job['count']:
                raise ValueError('Invalid source page.')
            donor = Image.open(working_original(job,donor_page)).convert('RGB')
            donor_box = word_repair.rectangle(data.get('donor_rect'),donor.size)
            if donor_page == index and box[0]<donor_box[2] and donor_box[0]<box[2] and box[1]<donor_box[3] and donor_box[1]<box[3]:
                raise ValueError('Choose a separate, undamaged occurrence of this word.')
        if data.get('method')=='fit':
            text=word_repair.confirmed_text(data.get('text'))
            source=Image.open(working_original(job,index)).convert('RGB')
            if job['options']['method'] in ('preset', 'huntington_background'):
                mask,_=known_mask(source,BASE/'assets/huntington.npy',min_score=.35)
            else:mask=paint_mask(source.size,job['options']['strokes'])
            x,y,xx,yy=box
            if not mask[y:yy,x:xx].any():raise ValueError('Select a word that intersects the watermark.')
            excluded=cv2.dilate(mask.astype('uint8'),np.ones((3,3),'uint8'))>0
            fitted=text_restore.fit(cv2.cvtColor(np.array(source),cv2.COLOR_RGB2GRAY),excluded,box,text)
            if fitted is None or fitted['score']<.85:
                raise ValueError('Too little matching original ink remains for a reliable font fit. Copy an intact occurrence or render confirmed text instead.')
            patch=Image.fromarray(fitted['gray']).convert('RGB')
            message='Fitted font, size, position and ink weight to surviving original letters. Confirm the wording and inspect the preview; shape similarity does not verify text.'
        elif data.get('method')=='lama':
            word_repair.confirmed_text(data.get('text'))
            source=Image.open(working_original(job,index)).convert('RGB')
            if job['options']['method'] in ('preset', 'huntington_background'):
                mask,_=known_mask(source,BASE/'assets/huntington.npy',min_score=.35)
            else:mask=paint_mask(source.size,job['options']['strokes'])
            scope=np.zeros_like(mask);x,y,xx,yy=box;scope[y:yy,x:xx]=mask[y:yy,x:xx]
            if not scope.any():raise ValueError('This word does not intersect the selected watermark. Use a clean occurrence or confirmed text.')
            pixels=neural_restore.inpaint(np.array(source),scope,BASE/'models/big-lama.pt')
            combined=np.array(current);combined[scope]=pixels[scope]
            patch=Image.fromarray(combined).crop(box)
            message='LaMa reconstructed the masked strokes. Your wording is a review reference, not a model instruction. Check each letter: AI can invent or omit strokes.'
        else:
            patch, message = word_repair.make_patch(current, box, data, donor)
        token = secrets.token_hex(16)
        job['pending_repair'] = dict(token=token,page=index,base=page_fingerprint(current),box=box,patch=patch,
                                     text=word_repair.confirmed_text(data['text']),method=data['method'])
        # Show context as well as letters, so baseline shifts and covered neighbours are visible.
        context = (max(0,box[0]-25),max(0,box[1]-12),min(current.width,box[2]+25),min(current.height,box[3]+12))
        after = current.copy()
        after.paste(patch,box)
        return jsonify(token=token,before=preview_data(current.crop(context)),after=preview_data(after.crop(context)),message=message)


@app.post('/api/repair/<identifier>/apply')
def word_apply(identifier):
    with LOCK:
        data = request.get_json()
        job,index = repair_job(identifier,data)
        pending = job.get('pending_repair')
        if not pending or data.get('token') != pending['token'] or pending['page'] != index:
            raise ValueError('Create a fresh repair preview first.')
        current = Image.open(image_path(job,index,True)).convert('RGB')
        if page_fingerprint(current) != pending['base']:
            raise ValueError('This page changed. Preview the repair again.')
        if data.get('confirmed') is not True:
            raise ValueError('Confirm the wording and appearance before applying.')
        history = job.setdefault('repairs',{}).setdefault(index,[])
        if len(history) >= 200:
            raise ValueError('This page has reached the limit of 200 word repairs.')
        history.append({k:pending[k] for k in ('box','patch','text','method')})
        current.paste(pending['patch'],pending['box'])
        current.save(image_path(job,index,True))
        job.pop('pending_repair',None)
        update_repair_result(job,index,current)
        return jsonify(public(job))


def update_repair_result(job,index,current):
    require_new_review(job,index)
    result = next(r for r in job['results'] if r['page']==index)
    result['repairs'] = len(job.get('repairs',{}).get(index,[]))
    result['changed'] = int(np.any(np.array(current)!=np.array(Image.open(original(job,index)).convert('RGB')),axis=2).sum())
    job.update(state='review',message='Word repair updated. Review and build the complete PDF again.')


@app.post('/api/repair/<identifier>/undo')
def word_undo(identifier):
    with LOCK:
        job,index = repair_job(identifier,request.get_json())
        history = job.get('repairs',{}).get(index,[])
        if not history:
            raise ValueError('There are no word repairs to undo on this page.')
        history.pop()
        require_new_review(job,index)
        result = process_page(job,index,job['options'])
        job['results'] = [result if r['page']==index else r for r in job['results']]
        job.update(state='review',message='Last word repair undone. Build the PDF again to save this version.')
        return jsonify(public(job))


@app.post('/api/repair/<identifier>/find')
def word_find(identifier):
    with LOCK:
        data = request.get_json()
        job,index = repair_job(identifier,data)
        source_page = data.get('donor_page',index)
        if type(source_page) is not int or not 0 <= source_page < job['count']:
            raise ValueError('Invalid source page.')
        text = word_repair.confirmed_text(data.get('text'))
        path = working_original(job,source_page)
        cache = source_ocr_cache(job,source_page,path)
        ocr=local_ocr(path,cache)
        image = Image.open(path).convert('RGB')
        mask,_ = known_mask(image,BASE/'assets/huntington.npy',min_score=.18)
        matches = word_repair.exact_matches(ocr,text,image.size,mask)
        found=[]
        for rect in matches:
            try: box=word_repair.rectangle(rect,image.size)
            except ValueError: continue
            found.append(dict(rect=rect,image=preview_data(image.crop(box))))
        return jsonify(matches=found)


@app.get('/api/repair/<identifier>/history')
def word_history(identifier):
    with LOCK:
        job = job_for(identifier)
        items = [dict(page=index+1,rectangle=list(r['box']),confirmed_text=r['text'],method=r['method'])
                 for index,repairs in job.get('repairs',{}).items() for r in repairs]
        items += [dict(page=index+1,rectangle=list(r['box']),matched_text=r['text'],method=r.get('method','automatic'),
                       donor_page=r['donor_page']+1 if 'donor_page' in r else None,
                       font=r.get('font'),match_score=r['score'])
                  for index,repairs in job.get('automatic',{}).items() for r in repairs]
        memory = io.BytesIO(json.dumps(dict(document=job['name'],repairs=items),indent=2).encode('utf-8'))
        return send_file(memory,as_attachment=True,download_name='word-repairs.json',mimetype='application/json')


@app.post('/api/preview/<identifier>')
def preview(identifier):
    with LOCK:
        job = job_for(identifier)
        if job['state'] in ('processing', 'exporting', 'learning', 'restoring'):
            raise ValueError('Wait for the current operation to finish.')
        data = request.get_json()
        options = settings(data)
        prepare_options(job, options)
        if options.get('scan_inversion') and 'inversion_model' not in job:
            raise ValueError('Process all pages once with PureScan enabled to learn this document’s watermark. Individual previews are available afterward.')
        index = int(data.get('page', 0))
        job['cancel'] = False
        try:
            result = process_page(job, index, options)
        except InterruptedError:
            raise ValueError('Preview stopped. The original document is unchanged.') from None
        # A preview belongs to new settings; old all-page results are no longer exportable.
        job.update(state='ready', results=[], options=options, message='Preview ready. Process all pages when satisfied.')
        job['ui']={}
        WORK['zip_ready']=False
        return jsonify(result)


def batch(job, options):
    try:
        if options.get('scan_inversion') and 'inversion_model' not in job:
            def pages():
                for index in range(job['count']):
                    with LOCK:
                        image=Image.open(working_original(job,index,options)).convert('RGB')
                    yield index,image
            def progress(index):
                job.update(progress=index+1,message=f'Learning the blended watermark: page {index+1} of {job["count"]}.')
            model=scan_inversion.learn_template(pages(),BASE/'assets/huntington.npy',lambda:job['cancel'],progress)
            if job['cancel']:
                raise InterruptedError()
            job['inversion_model']=model
        for index in range(job['count']):
            with LOCK:
                if job['cancel']:
                    job.update(state='cancelled', message='Stopped. The original PDF is unchanged.')
                    return
                try:
                    result = process_page(job, index, options)
                finally:
                    # PDF readers/writers contain cycles with large decoded
                    # streams. Collect only after the page work has unwound.
                    gc.collect()
                job['results'].append(result)
                job.update(progress=index+1, message=f'Processed page {index+1} of {job["count"]}.')
        if options.get('auto_restore'):
            job.update(state='restoring',progress=0,message='Finding intact words for automatic restoration...')
            restoration_worker(job)
        else:
            job.update(state='review', message='Review the results, then save your complete PDF.')
    except InterruptedError:
        job.update(state='cancelled', message='Stopped. The original PDF is unchanged.')
    except ValueError as error:
        job.update(state='error', message=str(error))
    except MemoryError:
        job.update(state='error', message=MEMORY_MESSAGE)
    except Exception as error:
        job.update(state='error', message='Processing failed. The original PDF is unchanged. Clear the document and try again.')


@app.post('/api/process/<identifier>')
def process(identifier):
    with LOCK:
        job = job_for(identifier)
        if job['state'] in ('processing', 'exporting', 'learning', 'restoring'):
            raise ValueError('An operation is already running.')
        options = settings(request.get_json())
        prepare_options(job, options)
        job.update(state='processing', progress=0, results=[], options=options, cancel=False, message='Starting cleanup...', ui={})
        WORK['zip_ready'] = False
        threading.Thread(target=batch, args=(job, options), daemon=True).start()
        return jsonify(public(job))


@app.post('/api/cancel/<identifier>')
def cancel(identifier):
    job_for(identifier)['cancel'] = True
    return jsonify(ok=True)


def require_ai_review(job, keep):
    if (job.get('options') or {}).get('method') in ('repeated_diagonal_ai', 'gemini_draft'):
        reviewed = set(job.get('ui', {}).get('reviewed', []))
        generated = {r['page'] for r in job.get('results', []) if r.get('reconstruction_unverified')}
        if (set(keep) & generated) - reviewed:
            raise ValueError('Review each selected AI draft and click Mark page reviewed before creating the PDF.')


def export_worker(job, keep):
    try:
        require_ai_review(job, keep)
        with LOCK:
            if job['options']['method'] in NATIVE_OUTLINE:
                module = NATIVE_OUTLINE[job['options']['method']]
                module.write_clean(job['source'], job['dir']/'cleaned.pdf', keep,
                                          job['dir'], lambda:job['cancel'])
            elif job['options']['method'] in nassau_scans.METHODS:
                nassau_scans.write_clean(job['source'], job['dir']/'cleaned.pdf', keep,
                                        job['dir'], lambda:job['cancel'])
            elif job['options']['method'] in preble_scans.METHODS:
                preble_scans.write_clean(job['source'], job['dir']/'cleaned.pdf', keep,
                                        job['dir'], lambda:job['cancel'])
            elif job['options']['method'] in dotted_scans.METHODS:
                dotted_scans.write_clean(job['source'], job['dir']/'cleaned.pdf', keep,
                                        job['options']['method'], job['dir'], lambda:job['cancel'])
            elif job['options']['method'] in ('unofficial_copy', 'red_copy', 'cumberland_red', 'flat_gray_copy', 'repeated_copy'):
                from clearpage.presets import gray_scans
                module, native_key = {'red_copy': (red_scans, 'native_red'),
                                      'cumberland_red': (red_scans, 'native_red'),
                                      'flat_gray_copy': (flat_gray_scans, 'native_flat_gray'),
                                      'repeated_copy': (repeated_copy, 'native_repeated_copy'),
                                      'unofficial_copy': (gray_scans, 'native_gray')}[job['options']['method']]
                results = {r['page']: r for r in job['results']}
                native = [i for i in keep if results.get(i, {}).get(native_key)]
                other = [i for i in keep if i not in native]
                target = job['dir']/('native-scan-export.pdf' if other else 'cleaned.pdf')
                extra = {'cumberland': True} if job['options']['method'] == 'cumberland_red' else {}
                module.write_clean(job['source'], target, native, job['dir'], lambda:job['cancel'], **extra)
                if other:
                    edits = {i: (original(job, i), image_path(job, i, True)) for i in other}
                    write_pdf(target, job['dir']/'cleaned.pdf', edits, lambda:job['cancel'])
            elif job['options']['method'] in ('overlay', 'object'):
                corrected = [i for i in keep if (job['results'][i].get('color_correction') or {}).get('applied')]
                target = job['dir']/('overlay-export.pdf' if corrected else 'cleaned.pdf')
                if job['options']['method']=='object':
                    pdf_selection.write_selected(job['source'],target,keep,job.get('object_selection',{}),lambda:job['cancel'])
                else:
                    pdf_overlays.write_clean(job['source'], target, keep, lambda: job['cancel'])
                if corrected:
                    edits = {i:(job['dir']/f'{i}-structural.png',image_path(job,i,True)) for i in corrected}
                    write_pdf(target,job['dir']/'cleaned.pdf',edits,lambda:job['cancel'])
            else:
                edits = {i: (original(job, i), image_path(job, i, True)) for i in keep}
                write_pdf(job['source'], job['dir']/'cleaned.pdf', edits, lambda: job['cancel'])
        job.update(state='done', export_keep=list(keep), message='Your complete PDF is ready to save.')
    except InterruptedError:
        job.update(state='review', message='Export cancelled. Your reviewed pages are still available.')
    except Exception:
        job.update(state='review', message='Could not export. Try keeping fewer cleaned pages.')


@app.post('/api/export/<identifier>')
def export(identifier):
    job = job_for(identifier)
    if job['state'] not in ('review', 'done'):
        raise ValueError('Process and review all pages before exporting.')
    values = request.get_json().get('keep', [])
    if not isinstance(values, list) or any(type(v) is not int or not 0 <= v < job['count'] for v in values):
        raise ValueError('Invalid page selection.')
    keep = sorted(set(values))
    require_ai_review(job, keep)
    job.update(state='exporting', cancel=False, message='Building the complete PDF...')
    threading.Thread(target=export_worker, args=(job, keep), daemon=True).start()
    return jsonify(public(job))


@app.get('/api/download/<identifier>')
def download(identifier):
    job = job_for(identifier)
    if job['state'] != 'done':
        raise ValueError('The PDF is not ready yet.')
    return send_file(job['dir']/'cleaned.pdf', as_attachment=True,
                     download_name=Path(job['name']).stem+f'_ClearPage-{VERSION}.pdf', mimetype='application/pdf')


def compression_worker(job, source, previous_state):
    def progress(value):
        with LOCK:
            job['compression']['progress'] = value
    try:
        result = pdf_compression.compress_pdf(source, job['dir']/'compressed.pdf',
                                             lambda: job['cancel'], progress)
        with LOCK:
            job['compression'].update(status='ready', progress=100, **result)
    except InterruptedError:
        with LOCK:
            job['compression'].update(status='cancelled', message='Compression stopped. You can try again.')
    except ValueError as error:
        with LOCK:
            job['compression'].update(status='error', message=str(error))
    except Exception:
        with LOCK:
            job['compression'].update(status='error', message='Could not compress this PDF. Check available disk space and try again.')
    finally:
        with LOCK:
            job['state'] = previous_state


@app.post('/api/compress/<identifier>')
def compress_document(identifier):
    with LOCK:
        job = job_for(identifier)
        if job['state'] not in ('ready', 'review', 'done', 'cancelled', 'error'):
            raise ValueError('Wait for the current operation to finish before compressing.')
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('source', 'original') not in ('original', 'cleaned'):
            raise ValueError('Choose the original PDF or a completed cleanup export.')
        source_kind = data.get('source', 'original')
        source = job['source'] if source_kind == 'original' else job['dir']/'cleaned.pdf'
        if source_kind == 'cleaned' and (job['state'] != 'done' or not source.is_file()):
            raise ValueError('Download your reviewed cleanup PDF first, then compress it.')
        previous_state = job['state']
        job.update(state='exporting', cancel=False,
                   compression=dict(status='processing', progress=0, source=source_kind))
        threading.Thread(target=compression_worker, args=(job, source, previous_state), daemon=True).start()
        return jsonify(job['compression'])


@app.get('/api/compress/<identifier>')
def compression_status(identifier):
    return jsonify(job_for(identifier).get('compression', dict(status='idle')))


@app.get('/api/compressed-download/<identifier>')
def compressed_download(identifier):
    job = job_for(identifier)
    if job.get('compression', {}).get('status') != 'ready':
        raise ValueError('Compress the PDF before downloading it.')
    return send_file(job['dir']/'compressed.pdf', as_attachment=True,
                     download_name=Path(job['name']).stem+'_ClearPage-compressed.pdf',
                     mimetype='application/pdf')


@app.post('/api/compare/<identifier>/<int:index>')
def compare_reference(identifier, index):
    with LOCK:
        job = job_for(identifier)
        if job['state'] not in ('review', 'done'):
            raise ValueError('Process all pages before comparing a reference.')
        original(job, index)
        target = job['dir']/'quality-reference.pdf'
        try:
            total = 0
            with target.open('wb') as output:
                while chunk := request.stream.read(1024*1024):
                    total += len(chunk)
                    if total > app.config['MAX_CONTENT_LENGTH']:
                        raise ValueError('Maximum file size is 200 MB.')
                    output.write(chunk)
            quality_check.validate_reference(job['source'], target)
            report = quality_check.compare(Image.open(original(job,index)),
                Image.open(image_path(job,index,True)), render_page(target,index))
            return jsonify(dict(page=index+1, comparison='Current cleaned preview versus supplied reference', **report))
        finally:
            target.unlink(missing_ok=True)


@app.post('/api/clear/<identifier>')
def clear(identifier):
    job = job_for(identifier)
    if job['state'] in ('processing', 'exporting', 'learning', 'restoring'):
        raise ValueError('Stop processing before clearing this document.')
    with LOCK:
        shutil.rmtree(job['dir'])
        del JOBS[identifier]
        WORK['zip_ready'] = False
    return jsonify(ok=True)


def learn_worker(job, reference):
    staging = job['dir']/'patches'
    staging.mkdir(exist_ok=True)
    pending = []
    try:
        with LOCK:
            count, sizes = inspect_pdf(reference)
            original_count, original_sizes = inspect_pdf(job['source'])
            if count != original_count or sizes != original_sizes:
                raise ValueError('Use the same pages, in the same order and with the same dimensions.')
        for index in range(job['count']):
            with LOCK:
                if job['cancel']:
                    raise InterruptedError('Import cancelled. No repairs were saved.')
                source = Image.open(original(job, index)).convert('RGB')
                result = render_page(reference, index)
                if result.size != source.size:
                    raise ValueError(f'Page {index+1}: page dimensions differ.')
                changed = np.any(np.array(source) != np.array(result), axis=2)
                if changed.any():
                    mask, score = known_mask(source, BASE/'assets/huntington.npy', min_score=.18)
                    allowed = cv2.dilate(mask.astype('uint8'), np.ones((11, 11), 'uint8')) > 0
                    fingerprint = page_fingerprint(source)
                    target = staging/(fingerprint+'.npz')
                    try:
                        save_reviewed_patch(source, result, allowed, target)
                    except ValueError as error:
                        raise ValueError(f'Page {index+1}: {error}') from error
                    pending.append((target, fingerprint))
                job.update(progress=index+1, message=f'Checking reviewed page {index+1} of {job["count"]}...')
        if job['cancel']:
            raise InterruptedError('Import cancelled. No repairs were saved.')
        if pending:
            PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
            for target, fingerprint in pending:
                os.replace(target, PROFILE_ROOT/(fingerprint+'.npz'))
        job.update(state='ready', results=[], message=f'Imported {len(pending)} reviewed pages. Process all pages to apply matching repairs.')
    except (ValueError, InterruptedError) as error:
        job.update(state='ready', message=str(error))
    except Exception:
        job.update(state='ready', message='Could not import the reviewed PDF. Check that it is the correct copy.')
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        reference.unlink(missing_ok=True)


@app.post('/api/reviewed/<identifier>')
def import_reviewed(identifier):
    with LOCK:
        job = job_for(identifier)
        if job['state'] in ('processing', 'exporting', 'learning', 'restoring'):
            raise ValueError('Wait for the current operation to finish.')
        target = job['dir']/'reviewed.pdf'
        try:
            with target.open('wb') as output:
                total = 0
                while chunk := request.stream.read(1024*1024):
                    total += len(chunk)
                    if total > app.config['MAX_CONTENT_LENGTH']:
                        raise ValueError('Maximum file size is 200 MB.')
                    output.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        job.update(state='learning', progress=0, results=[], cancel=False, message='Checking the reviewed copy...')
        job['ui']={}
        WORK['zip_ready']=False
        threading.Thread(target=learn_worker, args=(job, target), daemon=True).start()
        return jsonify(public(job))


@app.post('/api/quit')
def quit_app():
    WORK['stop'] = True
    for job in JOBS.values():
        job['cancel'] = True
    if SERVER:
        threading.Thread(target=SERVER.shutdown, daemon=True).start()
    return jsonify(ok=True)


def cleanup():
    with LOCK:
        shutil.rmtree(ROOT, ignore_errors=True)


atexit.register(cleanup)


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def main():
    global SERVER, HOST
    if '--release-self-test' in sys.argv:
        from clearpage.release_selftest import run
        run(BASE, Path(sys.argv[sys.argv.index('--release-self-test')+1]))
        return
    SERVER = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
    HOST = f'127.0.0.1:{SERVER.server_port}'
    url = f'http://{HOST}/'
    if '--test-info' in sys.argv:
        info = Path(sys.argv[sys.argv.index('--test-info')+1])
        info.write_text(json.dumps({'url': url}), encoding='utf-8')
        try:
            SERVER.serve_forever()
        finally:
            SERVER.server_close()
            cleanup()
        return
    worker = threading.Thread(target=SERVER.serve_forever, daemon=True)
    worker.start()
    from clearpage import auto_update
    updater = auto_update.start(sys.modules[__name__])
    try:
        from clearpage.desktop_shell import open_window
        if '--desktop-test-info' in sys.argv:
            Path(sys.argv[sys.argv.index('--desktop-test-info')+1]).write_text(
                json.dumps({'url': url}), encoding='utf-8')
        open_window(url)
    except Exception:
        from clearpage.desktop_shell import show_startup_error, write_startup_log
        log_path = write_startup_log()
        if '--desktop-smoke-info' not in sys.argv:
            show_startup_error(log_path)
        raise SystemExit(1)
    finally:
        if updater:
            updater.stop.set()
        WORK['stop'] = True
        for job in JOBS.values():
            job['cancel'] = True
        SERVER.shutdown()
        worker.join(timeout=10)
        SERVER.server_close()
        cleanup()


from clearpage import workspace
workspace.register(sys.modules[__name__])
from clearpage.billing import licensing
licensing.register(sys.modules[__name__])
from clearpage import object_routes
object_routes.register(sys.modules[__name__])


if __name__ == '__main__':
    main()
