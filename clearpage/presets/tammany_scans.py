"""Remove the St. Tammany gray stamp from native scan samples."""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path

import cv2
import numpy as np

PROBES = 'tammany-unofficial-probes.npz'
COVER_SHAPE = (3300, 2550)
COVER_CACHE = 'tammany-cover-union-v1.npy'
MAX_PIXELS = 12_000_000
STAMP = 160
SCAN_LEVELS = np.arange(16, dtype='uint8')*17
TINT_LEVELS = np.rint(np.arange(16)*STAMP/15).astype('uint8')
INVERSE = np.arange(256, dtype='uint8')
INVERSE[TINT_LEVELS] = SCAN_LEVELS
VALID_TINT = np.isin(np.arange(256), TINT_LEVELS)
EXCLUSIVE_TINT = VALID_TINT & ~np.isin(np.arange(256), SCAN_LEVELS)


@lru_cache(maxsize=1)
def probes():
    with np.load(RESOURCE_ROOT/'assets'/PROBES, allow_pickle=False) as archive:
        return archive['shapes']


def scan_object(page):
    objects = page.get('/Resources', {}).get('/XObject', {})
    if len(objects) != 1 or page.rotation:
        return None
    name = next(iter(objects))
    obj = objects[name].get_object()
    if (obj.get('/Subtype') != '/Image' or obj.get('/BitsPerComponent') != 8
            or obj.get('/Filter') != '/FlateDecode'
            or any(key in obj for key in ('/Mask', '/SMask', '/ImageMask', '/Decode'))):
        return None
    space = obj['/ColorSpace'] if '/ColorSpace' in obj else None
    if not (isinstance(space, list) and len(space) == 2 and space[0] == '/ICCBased'):
        return None
    channels = space[1].get_object().get('/N')
    height, width = int(obj.get('/Height', 0)), int(obj.get('/Width', 0))
    if channels == 3 and (height, width) != COVER_SHAPE:
        return None
    if channels == 1 and not (1000 <= width <= 3000 and 1.55 <= height/width <= 1.75):
        return None
    if channels not in (1, 3) or width*height > MAX_PIXELS:
        return None
    content = page.get_contents()
    if content is None or [args for args, op in content.operations if op == b'Do'] != [[name]]:
        return None
    return name, obj


def pixels(found):
    _, obj = found
    height, width = int(obj['/Height']), int(obj['/Width'])
    channels = obj['/ColorSpace'][1].get_object()['/N']
    try:
        data = obj.get_data()
    finally:
        obj.decoded_self = None
    if len(data) != height*width*channels:
        raise ValueError('The decoded scan dimensions do not match the PDF.')
    shape = (height, width, channels) if channels == 3 else (height, width)
    return np.frombuffer(data, dtype='uint8').reshape(shape)


def detected_stamp(a):
    if a.ndim == 2:
        mark = a == STAMP
        if np.any((a != 0) & ~mark & (a != 255)):
            return None
    else:
        mark = np.all(a == STAMP, axis=2)
    if not .025 <= np.count_nonzero(mark)/mark.size <= .08:
        return None
    reference = probes()[int(a.ndim == 3)]
    small = cv2.resize(mark.astype('uint8'), reference.shape[::-1], interpolation=cv2.INTER_AREA) > 0
    if np.count_nonzero(small & reference)/np.count_nonzero(small | reference) < .85:
        return None
    return mark


def cover_union(reader, mark, directory, cancelled):
    cache = Path(directory)/COVER_CACHE
    if cache.exists():
        return np.load(cache, allow_pickle=False)
    combined = mark.copy()
    for page in reader.pages:
        if cancelled():
            raise InterruptedError('Cancelled')
        found = scan_object(page)
        if not found or found[1]['/ColorSpace'][1].get_object()['/N'] != 3:
            continue
        other = detected_stamp(pixels(found))
        if other is not None and np.count_nonzero(mark & other)/np.count_nonzero(mark | other) >= .98:
            combined |= other
    temporary = cache.with_suffix('.part')
    try:
        with temporary.open('wb') as output:
            np.save(output, combined, allow_pickle=False)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return combined


def clean(reader, a, mark, directory, cancelled):
    result = a.copy()
    if a.ndim == 2:
        result[mark] = 255
        return result
    combined = cover_union(reader, mark, directory, cancelled)
    # A later cover can have the same lettering at a different native offset.
    if np.count_nonzero(mark & combined)/np.count_nonzero(mark | combined) < .98:
        combined = mark
    scope = cv2.morphologyEx(combined.astype('uint8'), cv2.MORPH_CLOSE, np.ones((23, 23), 'uint8'))
    near = cv2.dilate(scope, np.ones((19, 19), 'uint8')).astype(bool)
    gray = a[:, :, 0]
    neutral = np.all(a == gray[:, :, None], axis=2)
    selected = neutral & ((scope.astype(bool) & VALID_TINT[gray]) | (near & EXCLUSIVE_TINT[gray]))
    result[selected] = INVERSE[gray[selected], None]
    return result
