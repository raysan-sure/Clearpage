"""Lake County hollow UNOFFICIAL COPY on native bilevel scans."""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT

import cv2
import numpy as np
import pikepdf

from clearpage.presets import native_scan_profile as native

METHODS = ('lake_outline',)
CACHE = 'lake-outline-v1'
HEIGHT, WIDTH = 3100, 2400


@lru_cache(maxsize=1)
def profiles():
    with np.load(RESOURCE_ROOT/'assets/lake-outline.npz', allow_pickle=False) as data:
        masks = np.unpackbits(data['outlines'], axis=2)
    # Sample both the printed outline and neighboring paper in eight bands.
    probes = []
    for mask in masks:
        y, x = np.nonzero(mask)
        probes.append([(yy, xx) for yy, xx in zip(np.array_split(y[::4], 8),
                                                    np.array_split(x[::4], 8))])
    return masks, probes


def detect(page):
    found = native.image_entry(page, 1, {'/DeviceGray'}, {'/CCITTFaxDecode','/FlateDecode'},
                               lambda w,h: 2400 <= w <= 2700 and HEIGHT <= h <= 4500)
    if not found:
        return None
    name, obj = found
    ink = ~np.asarray(pikepdf.PdfImage(obj).as_pil_image().convert('1'), dtype=bool)
    masks, probes = profiles()
    scores = []
    for bands in probes:
        contrast = [ink[y,x].mean() - (ink[y,x+9].mean()+ink[y,x-9].mean())/2
                    for y,x in bands]
        scores.append(np.median(contrast) if np.count_nonzero(np.array(contrast)>.16)>=6 else 0.)
    variant = int(np.argmax(scores))
    return (name, ink, masks[variant], variant==2) if scores[variant]>.27 else None


def suggested(source, index):
    with pikepdf.open(source) as doc:
        return detect(doc.pages[index]) is not None


def band(mask, size):
    return cv2.dilate(mask.astype('uint8'), cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(size,size))).astype(bool)


def process(doc, index, directory, cancelled):
    found = detect(doc.pages[index])
    if found is None:
        return None
    name, ink, mask, warped = found
    area = ink[:HEIGHT,:WIDTH]
    scope = band(mask, 7 if warped else 5)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(area.astype('uint8'))
    overlap = np.bincount(labels[scope], minlength=count)
    exact = np.bincount(labels[mask>0], minlength=count)
    sizes = stats[:,-1]
    eligible = (overlap > .97*sizes) & (exact >= np.maximum(1,sizes*(.33 if warped else .53)))
    eligible[0] = False
    remove = eligible[labels] & scope
    remove |= area & scope & ~band(area & ~scope,9) & (scope if warped else band(mask,3))
    area[remove] = False
    return name, np.packbits(~ink,axis=1), 1, int(remove.sum())


def prepare(source,index,directory,cancelled=lambda:False):
    return native.prepare(source,index,directory,CACHE,process,cancelled)


def write_clean(source,target,keep,directory,cancelled=lambda:False):
    native.export_cached(source,target,keep,directory,CACHE,cancelled)
