"""Guarded native-image cleanup for the small repeated gray 'copy' pattern."""
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import io
import json
import cv2
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets import gray_scans

CACHE = 'native-repeated-copy-v1'
ASSET = RESOURCE_ROOT/'assets/repeated-copy.png'


def unchanged_details():
    return dict(matched=False, changed=0, uncertain=False, ai=False,
                pattern_note='No confident repeated gray “copy” match. Page kept unchanged.')


def clean(image, cancelled=lambda: False):
    if cancelled(): raise InterruptedError('Cancelled')
    a = np.array(image.convert('RGB')); h, w = a.shape[:2]
    details = unchanged_details()
    empty = np.zeros((h, w), bool)
    if min(h, w) < 200 or h*w > 12_000_000:
        return a, empty, details
    if np.mean(a.max(2).astype(int)-a.min(2).astype(int) > 20) > .02:
        return a, empty, details
    g = a.min(2)
    if np.mean(g > 235) < .35: return a, empty, details
    with Image.open(ASSET) as opened: base = np.array(opened.convert('RGB'))
    pad = 200
    obs = np.pad(cv2.GaussianBlur(((g>155)&(g<235)).astype('float32'), (3,3), 0), pad)
    def correlate(tile):
        gray = tile.min(2)
        tm = cv2.GaussianBlur(((gray>155)&(gray<235)).astype('float32'), (3,3), 0)
        return cv2.matchTemplate(obs, tm, cv2.TM_CCOEFF_NORMED)
    scores = correlate(base)
    scale = 1.
    if scores.max() < .70:
        best = (float(scores.max()), base, scores, scale)
        for candidate in (.8,.9,1.1,1.2,1.3,1.4,1.5,1.6,1.7,1.8):
            if cancelled(): raise InterruptedError('Cancelled')
            tile = cv2.resize(base, None, fx=candidate, fy=candidate)
            scoremap = correlate(tile)
            if scoremap.max() > best[0]: best = (float(scoremap.max()), tile, scoremap, candidate)
        _, base, scores, scale = best
    if scores.max() < .70: return a, empty, details
    template = np.full((h+2*pad,w+2*pad,3),255,'float32')
    hits = []
    for _ in range(150):
        if cancelled(): raise InterruptedError('Cancelled')
        _, score, _, (x,y) = cv2.minMaxLoc(scores)
        if score < .56: break
        hits.append((x-pad,y-pad,score))
        tile = template[y:y+base.shape[0],x:x+base.shape[1]]
        np.minimum(tile,base,out=tile)
        scores[max(0,y-60):y+61,max(0,x-60):x+61] = -1
    # Require many independently recognized glyphs spread over the page.
    strong = [hit for hit in hits if hit[2] >= .70]
    if (len(hits)<15 or len(strong)<5 or
            np.ptp([hit[0] for hit in strong]) < .35*w or
            np.ptp([hit[1] for hit in strong]) < .35*h):
        return a, empty, details
    template = template[pad:pad+h,pad:pad+w]
    scope = cv2.dilate((template.min(2)<248).astype('uint8'),np.ones((5,5),'uint8'))>0
    alpha = np.clip((255-template)/85,0,.85)
    result = np.clip(np.rint((a.astype('float32')-170*alpha)/(1-alpha)),0,255).astype('uint8')
    result[~scope] = a[~scope]
    ink = cv2.dilate((result.min(2)<180).astype('uint8'),np.ones((3,3),'uint8'))>0
    result[scope & ~ink] = 255
    changed = np.any(result != a,axis=2)
    details.update(matched=True,changed=int(changed.sum()),uncertain=True,
                   copies=len(hits),scale=scale,outside_mask_exact=True,
                   pattern_note='Repeated gray “copy” cleanup applied without AI. Review overlaps and dark headers: some marks may remain. Original security printing is not targeted; pixels outside the matched areas are unchanged.')
    return result, changed, details


def scan_object(page):
    direct = gray_scans.scan_object(page)
    if direct: return None, direct[0], direct[1]
    resources = page.get('/Resources', {})
    patterns = resources.get('/Pattern', {})
    if len(patterns) != 1: return None
    key = next(iter(patterns)); pattern = patterns[key].get_object()
    if pattern.get('/PatternType') != 1 or pattern.get('/PaintType') != 1: return None
    objects = pattern.get('/Resources', {}).get('/XObject', {})
    if not 1 <= len(objects) <= 3: return None
    main = []
    for name, ref in objects.items():
        obj = ref.get_object()
        if (obj.get('/Subtype') != '/Image' or obj.get('/BitsPerComponent') != 8 or
                obj.get('/ColorSpace') != '/DeviceRGB' or
                any(k in obj for k in ('/Mask','/SMask','/Decode','/ImageMask'))): return None
        w,h = int(obj.get('/Width',0)),int(obj.get('/Height',0))
        if min(w,h)<=0 or w*h>12_000_000: return None
        if min(w,h)>100: main.append((name,obj))
        elif min(w,h)!=1: return None
    if len(main)!=1: return None
    return key, *main[0]


def pixels(found):
    obj = found[2]; w,h = int(obj['/Width']),int(obj['/Height'])
    data = obj.get_data()
    if len(data)==w*h*3: return Image.fromarray(np.frombuffer(data,np.uint8).reshape(h,w,3))
    with Image.open(io.BytesIO(data)) as opened:
        result = opened.convert('RGB')
    if result.size != (w,h): raise ValueError('Scan dimensions do not match.')
    return result


def replace(writer, page, found, a):
    # Copy every resource container on the modified path so shared pages stay intact.
    pattern_key, name, _ = found
    resources = DictionaryObject(page['/Resources'])
    page[NameObject('/Resources')] = resources
    owner = resources
    if pattern_key is not None:
        patterns = DictionaryObject(resources['/Pattern'])
        resources[NameObject('/Pattern')] = patterns
        old = patterns[pattern_key].get_object()
        pattern = DecodedStreamObject()
        pattern.update({k:v for k,v in old.items() if k not in ('/Filter','/DecodeParms','/Length')})
        pattern.set_data(old.get_data())
        patterns[NameObject(pattern_key)] = writer._add_object(pattern)
        owner = DictionaryObject(old['/Resources'])
        pattern[NameObject('/Resources')] = owner
    objects = DictionaryObject(owner['/XObject'])
    owner[NameObject('/XObject')] = objects
    # Replace only the recognized main image. Edge strips are separate source data.
    old = objects[name].get_object()
    image = DecodedStreamObject()
    image.update({k:v for k,v in old.items() if k not in ('/Filter','/DecodeParms','/Length')})
    image.set_data(a.tobytes())
    objects[NameObject(name)] = writer._add_object(image.flate_encode())


def prepare(source,index,directory,cancelled=lambda:False):
    if cancelled(): raise InterruptedError('Cancelled')
    target = Path(directory)/f'{index}-{CACHE}.pdf'; meta = target.with_suffix('.json')
    if target.exists() and meta.exists(): return target,json.loads(meta.read_text())
    # PdfReader owns a copy of the entire input stream. Its indirect objects form
    # cycles: dropping only the page variable does not promptly release that copy.
    with read_pdf(source) as reader:
        page = reader.pages[index]; found = scan_object(page)
        if found is None: return None
        result, changed, details = clean(pixels(found),cancelled)
        writer = PdfWriter(); writer.add_page(page)
        if changed.any(): replace(writer,writer.pages[0],found,result)
        details['native_repeated_copy'] = True
        if cancelled(): raise InterruptedError('Cancelled')
        temporary = target.with_suffix('.part')
        try:
            with temporary.open('wb') as stream: writer.write(stream)
            temporary.replace(target); meta.write_text(json.dumps(details))
        finally: temporary.unlink(missing_ok=True)
    return target,details


def write_clean(source,target,keep,directory,cancelled=lambda:False):
    with read_pdf(source) as reader:
        writer = PdfWriter(); writer.clone_document_from_reader(reader)
        for index in keep:
            if cancelled(): raise InterruptedError('Cancelled')
            with read_pdf(Path(directory)/f'{index}-{CACHE}.pdf') as cache:
                found = scan_object(cache.pages[0]); original = scan_object(writer.pages[index])
                if found is None or original is None: raise ValueError('Invalid repeated copy cache. Process again.')
                replace(writer,writer.pages[index],original,np.array(pixels(found)))
        with open(target,'wb') as stream: writer.write(stream)
