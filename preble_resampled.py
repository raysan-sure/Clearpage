"""Detect and remove the resampled Preble halftone on grayscale RGB scans."""
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2 as cv
import numpy as np
import pikepdf

from native_scan_profile import image_entry


def entry(page):
    return image_entry(page, 8, {'/DeviceRGB'}, {'/FlateDecode'},
                       lambda w,h: 1500 <= w <= 3000 and 1900 <= h <= 4500 and w*h <= 12_000_000)


def pixels(obj):
    rgb = np.asarray(pikepdf.PdfImage(obj).as_pil_image())
    if rgb.ndim == 3 and rgb.shape[2] == 3 and np.array_equal(rgb[:,:,0],rgb[:,:,1]) and np.array_equal(rgb[:,:,1],rgb[:,:,2]):
        return rgb[:,:,0].copy()
    return None


def texture_scope(gray):
    closed = cv.morphologyEx(gray, cv.MORPH_CLOSE, np.ones((5,5),'uint8'))
    size = (gray.shape[1]//4, gray.shape[0]//4)
    texture = cv.resize(closed-gray, size, interpolation=cv.INTER_AREA)
    tone = cv.resize(gray, size, interpolation=cv.INTER_AREA)
    seeds = ((texture>27) & (tone>118) & (tone<227)).astype('uint8')
    seeds = (cv.boxFilter(seeds,cv.CV_32F,(7,7))>.57).astype('uint8')
    _,labels,stats,_ = cv.connectedComponentsWithStats(seeds)
    keep = (stats[:,-1]>=27) & (stats[:,2:4].max(axis=1)>=8)
    keep[0] = False
    y,x = np.nonzero(keep[labels])
    if len(x)<600:
        return None
    best = (-1,0,0)
    for slope in np.linspace(.70,1.35,66):
        hist = np.bincount(np.rint(x+slope*y).astype(int))
        weight = np.convolve(hist,np.ones(31),'same')
        center = np.argmax(weight)
        if weight[center]>best[0]:
            best = weight[center],center,slope
    _,center,slope = best
    good = np.zeros_like(seeds)
    within = np.abs(x+slope*y-center)<29
    good[y[within],x[within]] = 1
    nearby = cv.dilate(good,np.ones((17,17),'uint8'))
    shape = ((nearby>0) & (texture>17) & (tone<243)).astype('uint8')
    shape = cv.morphologyEx(shape,cv.MORPH_CLOSE,np.ones((5,5),'uint8'))
    _,labels = cv.connectedComponents(shape)
    ids = np.unique(labels[good>0])
    mask = np.isin(labels,ids[ids!=0]).astype('uint8')
    return cv.dilate(mask,np.ones((3,3),'uint8')),good


@lru_cache(maxsize=1)
def template():
    with np.load(Path(__file__).parent/'assets/preble-resampled.npz',allow_pickle=False) as data:
        return data['template']


def locate(gray):
    region = texture_scope(gray)
    if region is None:
        return None
    texture, seeds = region
    small = cv.resize(255-gray,None,fx=.125,fy=.125,interpolation=cv.INTER_AREA)
    margin = 75
    field = np.pad(np.minimum(small,92),margin)
    pattern = template()
    best = None
    def trial(scale, aspect):
        nonlocal best
        glyph = cv.resize(pattern,None,fx=scale*aspect,fy=scale)
        if any(a>b for a,b in zip(glyph.shape,field.shape)):
            return
        _,score,_,position = cv.minMaxLoc(cv.matchTemplate(field,glyph,cv.TM_CCOEFF_NORMED))
        if best is None or score>best[0]:
            best = score,scale,position,glyph
    for scale in np.arange(.72,1.66,.04):
        trial(scale,1)
    if best is None:
        return None
    for scale in np.arange(best[1]-.045,best[1]+.046,.008):
        for aspect in (.97,.985,1,1.015,1.03):
            trial(scale,aspect)
    score,_,(x,y),glyph = best
    if score<.23:
        return None
    aligned = np.zeros_like(field)
    aligned[y:y+glyph.shape[0],x:x+glyph.shape[1]] = glyph>4
    aligned = cv.dilate(aligned[margin:-margin,margin:-margin],np.ones((5,5),'uint8'))
    aligned = cv.resize(aligned,texture.shape[::-1],interpolation=cv.INTER_NEAREST)
    # Require the periodic texture to agree with the word shape across the page.
    supported = aligned.astype(bool) & (seeds>0)
    if supported.sum()<550 or supported.sum()<.50*seeds.sum():
        return None
    return texture | aligned


def suggested(source,index):
    with pikepdf.open(source) as doc:
        found = entry(doc.pages[index])
        gray = pixels(found[1]) if found else None
        return gray is not None and locate(gray) is not None


def paper_reference(doc, gray, directory, cancelled):
    # Store only a per-job estimate shared by distinct scans of the same size.
    h,w = gray.shape
    cache = Path(directory)/f'preble-paper-v1-{w}x{h}.npz'
    if not cache.exists():
        candidates = []
        for page in doc.pages:
            objs = page.Resources.get('/XObject',{})
            if len(objs)==1:
                obj = next(iter(objs.values()))
                if (obj.get('/Width'),obj.get('/Height'))==(w,h):
                    candidates.append(page)
        if len(candidates)<9:
            return None
        fingerprints = set()
        background = None
        for page in candidates:
            if cancelled():
                raise InterruptedError('Cancelled')
            found = entry(page)
            other = pixels(found[1]) if found else None
            if other is None:
                continue
            fingerprint = other[::32,::32].tobytes()
            if fingerprint in fingerprints or texture_scope(other) is None:
                continue
            fingerprints.add(fingerprint)
            background = other if background is None else np.maximum(background,other,out=background)
            if len(fingerprints)>=32:
                break
        valid = len(fingerprints)>=9 and locate(background) is not None
        with NamedTemporaryFile(dir=directory,delete=False) as handle:
            tmp = Path(handle.name)
        try:
            with tmp.open('wb') as handle:
                np.savez_compressed(handle, **({'paper':background} if valid else {}))
            tmp.replace(cache)
        finally:
            tmp.unlink(missing_ok=True)
    with np.load(cache,allow_pickle=False) as data:
        return data['paper'] if 'paper' in data else None


def clean(gray, mask, paper):
    scope = cv.resize(mask,gray.shape[::-1],interpolation=cv.INTER_NEAREST)>0
    ink = 255-gray
    horizontal = cv.morphologyEx(ink,cv.MORPH_OPEN,np.ones((1,3),'uint8'))
    square = np.ones((2,2),'uint8')
    solid = cv.dilate(cv.erode(ink,square,anchor=(0,0)),square,anchor=(1,1))
    support = np.maximum(horizontal,solid)
    near = cv.dilate((support>=176).astype('uint8'),np.ones((5,5),'uint8'))>0
    value = np.minimum(ink,cv.dilate(support,np.ones((3,3),'uint8')))
    edge = 255-np.clip((value.astype('float32')-87)*255/168,0,255)
    corrected = np.where(support>=176,gray,np.where(near,edge,255)).astype('uint8')
    result = gray.copy()
    if paper is None:
        result[scope] = corrected[scope]
        return result
    inside = (paper<255) & scope
    # A different screen phase must not use this reference.
    if not inside.any() or np.mean(paper[inside]==gray[inside])<.5:
        result[scope] = corrected[scope]
        return result
    level = paper[inside].astype('float32')
    values = corrected[inside]
    if np.all((paper==0)|(paper==255)):
        visible = (~inside).astype('float32')
        weight = cv.GaussianBlur(visible,(5,5),.8)
        light = cv.GaussianBlur(gray*visible,(5,5),.8)/np.maximum(weight,.001)
        values = np.where(light[inside]<132,0,255).astype('uint8')
    good = level>18
    values[good] = np.minimum(255,np.rint(gray[inside][good]*255./level[good])).astype('uint8')
    result[inside] = values
    return result


def process(doc,index,directory,cancelled):
    found = entry(doc.pages[index])
    gray = pixels(found[1]) if found else None
    mask = locate(gray) if gray is not None else None
    if mask is None:
        return None
    result = clean(gray,mask,paper_reference(doc,gray,directory,cancelled))
    return found[0],result,8,int(np.count_nonzero(result!=gray))
