"""Native grayscale cleanup for the supported dotted search-package scans."""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import hashlib

import cv2
import numpy as np
from pypdf.generic import DecodedStreamObject

from clearpage.pdf.pdf_overlays import read_pdf

SHAPE = (1800, 1500)
MODEL_NAME = 'search-package-dots-v1.npz'
PROBE_HASH = '96e1686d088848bfb731e2f86a56aa99a70c9bf8dbf8bb5df5c0b2a80393f5d9'
REVIEWED_SINGLETON = 'f3dd22dd97b34a695e6038da5e5f93ded83b4f1ddbca2c6599e18d38d9584047'
REFERENCE_VARIANTS = (0, 1, 4)


@lru_cache(maxsize=1)
def probes():
    path = RESOURCE_ROOT/'assets/search-package-dot-probes.npz'
    if hashlib.sha256(path.read_bytes()).hexdigest() != PROBE_HASH:
        raise ValueError('The dotted search-package profile is damaged. Reinstall ClearPage.')
    with np.load(path, allow_pickle=False) as data:
        y, x = data['coords'].T
        return y, x, data['expected'].astype('int16')


def scan_object(page):
    resources = page.get('/Resources', {})
    objects = resources.get('/XObject', {})
    if len(objects) != 1 or page.rotation:
        return None
    name = next(iter(objects))
    image = objects[name].get_object()
    if (image.get('/Subtype') != '/Image'
            or (image.get('/Height'), image.get('/Width')) != SHAPE
            or image.get('/BitsPerComponent') != 8
            or image.get('/Filter') != '/FlateDecode'
            or any(key in image for key in ('/Mask', '/SMask', '/ImageMask', '/Decode'))):
        return None
    color = image.get('/ColorSpace')
    if not (color == '/DeviceRGB' or
            (isinstance(color, list) and len(color) == 2 and color[0] == '/ICCBased'
             and color[1].get_object().get('/N') == 3)):
        return None
    content = page.get_contents()
    if content is None:
        return None
    allowed = {b'cm', b'q', b'Q', b're', b'W', b'W*', b'n', b'RG', b'rg', b'gs', b'BDC', b'EMC', b'Do'}
    operations = content.operations
    if any(op not in allowed for _, op in operations):
        return None
    if [args for args, op in operations if op == b'Do'] != [[name]]:
        return None
    return name, image


def native_gray(page, found):
    _, image = found
    try:
        decoded = page.images[0].image
        if decoded.mode != 'RGB':
            return None
        pixels = np.asarray(decoded)
        gray = pixels[:, :, 0]
        if gray.shape != SHAPE or any(not np.array_equal(gray, pixels[:, :, c]) for c in (1, 2)):
            return None
        return gray.copy()
    finally:
        # pypdf caches decoded streams on its reader. Keep only one native
        # page in memory while accumulating references for a long package.
        image.decoded_self = None


def variant(pixels):
    if pixels is None:
        return -1
    y, x, expected = probes()
    agreement = (np.abs(expected - pixels[y, x]) <= 2).reshape(5, 4, -1).mean(axis=2)
    scores = agreement.mean(axis=1)
    best = int(scores.argmax())
    if scores[best] < .80 or agreement[best].min() < .55 or scores[best]-np.sort(scores)[-2] < .16:
        return -1
    return best


def neighborhood(template, size, threshold):
    return cv2.dilate((template < threshold).astype('uint8'), np.ones((size, size), 'uint8')).astype(bool)


def singleton_template(source, template, references):
    """Use the reviewed page's blank rows to estimate its distinct dot phase."""
    scope = neighborhood(np.minimum.reduce(references), 5, 255)
    y, x = np.nonzero(scope)
    features = np.stack([ref[y+dy, x+dx] for ref in references
                         for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=1).astype('float32')/255
    pixels = source[y, x]
    train = (y < 195) | (y > 475)
    query = ~train
    index = cv2.flann_Index(np.ascontiguousarray(features[train]), dict(algorithm=1, trees=4))
    neighbors, distances = index.knnSearch(np.ascontiguousarray(features[query]), 5, params=dict(checks=160))
    weights = (.006 + np.sqrt(distances))**-3
    guess = (pixels[train][neighbors]*weights).sum(axis=1)/weights.sum(axis=1)
    yy, xx = y[query], x[query]
    transmission = np.maximum(pixels[query], np.rint(guess))
    off = (transmission > 247) & (np.min(references[:, yy, xx], axis=0) > 245)
    transmission[off] = 255
    ink = (source < 238) & ~scope
    distance = cv2.distanceTransform((~ink).astype('uint8'), cv2.DIST_L2, 5)
    blank = (distance[yy, xx] >= 3.5) & (guess < 254.6)
    transmission[blank] = pixels[query][blank]
    template[195:476] = 255
    template[yy, xx] = transmission.astype('uint8')


def build_model(source, cancelled):
    reader = read_pdf(source)
    variants = np.full(len(reader.pages), -1, dtype='int8')
    brightest = np.zeros((5, *SHAPE), dtype='uint8')
    reviewed = None
    for i, page in enumerate(reader.pages):
        if cancelled():
            raise InterruptedError('Cancelled')
        found = scan_object(page)
        if not found:
            continue
        pixels = native_gray(page, found)
        kind = variant(pixels)
        if kind < 0:
            continue
        variants[i] = kind
        np.maximum(brightest[kind], pixels, out=brightest[kind])
        if kind == 2 and hashlib.sha256(pixels.tobytes()).hexdigest() == REVIEWED_SINGLETON:
            reviewed = pixels
    counts = np.bincount(variants[variants >= 0], minlength=5)
    if any(counts[kind] < 3 for kind in REFERENCE_VARIANTS):
        return np.full_like(variants, -1), {}
    # An isolated variant needs independent references, except for the
    # exact reviewed page whose safe training rows are known.
    if counts[2] < 2 and reviewed is None:
        variants[variants == 2] = -1
    if counts[3] < 2:
        variants[variants == 3] = -1
    present = set(int(kind) for kind in variants if kind >= 0)
    first = np.maximum(brightest[0], brightest[2]) if 2 in present else brightest[0]
    samples = [first, brightest[1], brightest[4]]
    if 3 in present:
        samples.append(brightest[3])
    shared = np.sum([neighborhood(sample, 5, 252) for sample in samples], axis=0) >= 3
    y, x = np.ogrid[:SHAPE[0], :SHAPE[1]]
    shared &= np.abs(y-(1.275*x-65)) <= 175
    samples = [np.where(shared, sample, 255) for sample in samples]
    scope = np.sum([neighborhood(sample, 7, 251) for sample in samples], axis=0) >= 2
    templates = {kind: np.where(scope, brightest[kind], 255) for kind in present}
    # Reject inconsistent geometry or implausibly extensive masks.
    for kind, template in list(templates.items()):
        yy, xx = np.nonzero(template < 255)
        if (not 10000 <= len(yy) <= 27000 or yy.min() < 10 or xx.min() < 10
                or yy.max() >= SHAPE[0]-10 or xx.max() >= SHAPE[1]-10):
            del templates[kind]
            variants[variants == kind] = -1
    if any(kind not in templates for kind in REFERENCE_VARIANTS):
        return np.full_like(variants, -1), {}
    if 2 in templates and reviewed is not None and counts[2] == 1:
        singleton_template(reviewed, templates[2], np.stack([templates[k] for k in REFERENCE_VARIANTS]))
    return variants, {str(kind): template for kind, template in templates.items()}


def template_for(source, index, directory, cancelled):
    cache = Path(directory)/MODEL_NAME
    if not cache.exists():
        variants, templates = build_model(source, cancelled)
        temporary = cache.with_suffix('.part')
        try:
            if cancelled():
                raise InterruptedError('Cancelled')
            with temporary.open('wb') as file:
                np.savez_compressed(file, variants=variants, **templates)
            if cancelled():
                raise InterruptedError('Cancelled')
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
    with np.load(cache, allow_pickle=False) as data:
        kind = int(data['variants'][index])
        return data[str(kind)] if kind >= 0 else None


def restore(pixels, template):
    # Estimate the ink through the mark, then reconstruct saturated dots
    # from matching strokes on opposite sides. Only the template can change.
    y, x = np.nonzero(template < 255)
    transmission = template[y, x].astype('float32')
    observed = pixels[y, x].astype('float32')
    inverse = np.divide(observed*255, transmission, out=np.full_like(observed, 255), where=transmission != 0)
    total = np.zeros_like(observed)
    weight = np.zeros_like(observed)
    bridge = np.full_like(observed, 255)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1), (1, 2), (2, 1), (1, -2), (2, -1)):
        values, distances = [], []
        for sign in (-1, 1):
            samples = np.full_like(observed, 255)
            distance = np.full(observed.shape, 6, dtype='int16')
            for step in range(5, 0, -1):
                yy, xx = y+sign*step*dy, x+sign*step*dx
                clear = template[yy, xx] == 255
                samples[clear] = pixels[yy[clear], xx[clear]]
                distance[clear] = step
            values.append(samples)
            distances.append(distance)
        left, right = values
        ldist, rdist = distances
        span = np.hypot(dy, dx)*(ldist+rdist)
        interp = (left*rdist+right*ldist)/(ldist+rdist)
        confidence = np.exp(-np.abs(left-right)/34)/span**2
        confidence[(ldist == 6) | (rdist == 6)] = 0
        total += interp*confidence
        weight += confidence
        weaker = np.maximum(left, right)
        valid = (np.maximum(ldist, rdist) <= 3) & (weaker < 155)
        bridge = np.minimum(bridge, np.where(valid, weaker+7*(span-2), 255))
    estimate = np.divide(total, weight, out=np.full_like(total, 255), where=weight > 0)
    estimate = np.minimum(estimate, bridge)
    estimate = np.where((observed == transmission) & (bridge == 255), 255, estimate)
    blend = (np.minimum(transmission/105, 1))**.8
    correction = estimate*(1-blend)+inverse*blend
    correction = np.minimum(correction, np.where((bridge < 72) & (observed < 90), np.maximum(observed, bridge)+22, 255))
    result = pixels.copy()
    result[y, x] = np.rint(np.clip(correction, observed, observed+255-transmission)).astype('uint8')
    return result


def image_stream(cloned, pixels):
    stream = DecodedStreamObject()
    stream.update({key: value for key, value in cloned.items() if key not in ('/Filter', '/DecodeParms', '/Length')})
    stream.set_data(np.repeat(pixels[:, :, None], 3, axis=2).tobytes())
    return stream.flate_encode()
