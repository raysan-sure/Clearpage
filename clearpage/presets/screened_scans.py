"""Native cleanup of the verified diagonal dotted 'Un-Official' screen.

The screen overwrites some ink with a periodic black/white pattern. Filters
interpolate the surviving samples; this is reconstruction, not exact recovery.
Only the supported geometry and screen qualify. No OCR, cloud service, or model
runtime is needed. Reviewed word patches require an exact native-page hash.
"""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import hashlib
import json
import cv2
import numpy as np
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, NumberObject
from clearpage.presets.dotted_scans import replace_image
from clearpage.pdf.pdf_overlays import read_pdf

METHODS = ('screened_unofficial',)
CACHE = 'screened-unofficial-v2'
SHAPE = (2160, 1728)
PROFILE_SHA256 = '26f1afcbba62c2d087daf36ad024cbf379afeaf9fdc9cadb05ba5bacf4215ef1'


@lru_cache(maxsize=1)
def profile():
    path = RESOURCE_ROOT/'assets/screened-unofficial.npz'
    if hashlib.sha256(path.read_bytes()).hexdigest() != PROFILE_SHA256:
        raise ValueError('The dotted Un-Official profile is damaged. Reinstall ClearPage.')
    with np.load(path, allow_pickle=False) as data:
        mask = np.unpackbits(data['mask'], axis=1).astype(bool)
        phase, kernels = data['phase'].copy(), data['kernels'].astype('float32')
        boxes = data['boxes'].tolist()
        patches = [data[f'patch_{i}'].copy() for i in range(len(boxes))]
        fingerprint = str(data['fingerprint'])
    scope = cv2.morphologyEx(mask.astype('uint8'), cv2.MORPH_CLOSE, np.ones((7, 7), 'uint8')).astype(bool)
    inside = cv2.erode(scope.astype('uint8'), np.ones((9, 9), 'uint8')).astype(bool)
    signal = np.tile(phase, (SHAPE[0]//6, SHAPE[1]//6))
    forbidden = inside & ~mask & ~signal
    # Work only on the small watermark area, not 36 full-page temporary grids.
    y, x = np.nonzero(scope)
    samples = []
    for py in range(6):
        for px in range(6):
            selected = (y % 6 == py) & (x % 6 == px)
            kernel = kernels[py*6+px]
            dy, dx = np.nonzero(kernel)
            samples.append((y[selected], x[selected], dy-5, dx-5, kernel[dy, dx]))
    return dict(mask=mask, scope=scope, forbidden=forbidden, samples=samples,
                fingerprint=fingerprint, boxes=boxes, patches=patches)


def scan_objects(page):
    """One native scan or two equal bands, each drawn once in stream order."""
    objects = page.get('/Resources', {}).get('/XObject', {})
    if len(objects) not in (1, 2):
        return None
    content = page.get_contents()
    if content is None:
        return None
    draws = [args[0] for args, op in content.operations if op == b'Do' and len(args) == 1]
    if len(draws) != len(objects) or len(set(draws)) != len(objects) or set(draws) != set(objects):
        return None
    bands = []
    for name in draws:
        obj = objects[name].get_object()
        if (obj.get('/Subtype') != '/Image' or obj.get('/ColorSpace') != '/DeviceGray'
                or obj.get('/BitsPerComponent') not in (1, 8)
                or int(obj.get('/Width', 0)) != SHAPE[1]
                or int(obj.get('/Height', 0)) != SHAPE[0]//len(draws)
                or any(key in obj for key in ('/Mask', '/SMask', '/ImageMask'))
                or list(obj.get('/Decode', [0, 1])) != [0, 1]):
            return None
        bands.append((name, obj))
    return bands


def native_pixels(page, bands):
    parts = []
    for name, obj in bands:
        a = np.array(page.images[name].image.convert('L'))
        if a.shape != (int(obj['/Height']), int(obj['/Width'])) or np.any((a != 0) & (a != 255)):
            return None
        parts.append(a == 0)
    return np.vstack(parts)


def matches(scan):
    if scan is None or scan.shape != SHAPE or scan.dtype != np.bool_:
        return False
    data = profile()
    # Exact periodic pattern: a clean page, shifted mark, ordinary halftone,
    # or different grayscale screen must not receive these reconstruction rules.
    return bool(scan[data['mask']].all() and not scan[data['forbidden']].any())


def unchanged_details():
    return dict(native_screened=True, scan_pattern=True, matched=False, changed=0,
                confidence=None, protected=0, reviewed=False, uncertain=False, ai=False,
                reconstructed=0, reviewed_word_repairs=0,
                pattern_note='Original page kept: this page does not match the supported dotted Un-Official scan.')


def clean_pixels(scan, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    details = unchanged_details()
    if not matches(scan):
        return scan, details
    data = profile()
    result = scan.copy()
    for y, x, dy, dx, weights in data['samples']:
        if cancelled():
            raise InterruptedError('Cancelled')
        estimate = np.zeros(len(y), dtype='float32')
        for sy, sx, weight in zip(dy, dx, weights):
            estimate += scan[y+sy, x+sx].astype('float32')*weight
        result[y, x] = estimate >= .4
    result |= scan & ~data['mask']
    repairs = 0
    if hashlib.sha256(scan.tobytes()).hexdigest() == data['fingerprint']:
        for box, patch in zip(data['boxes'], data['patches']):
            x, y, xx, yy = box
            selected = data['scope'][y:yy, x:xx]
            block = result[y:yy, x:xx]
            repairs += int(np.any(block[selected] != patch[selected]))
            block[selected] = patch[selected]
    if cancelled():
        raise InterruptedError('Cancelled')
    # These checks run on exports as well as previews; never dim surviving ink.
    if np.any(scan & ~data['mask'] & ~result) or not np.array_equal(result[~data['scope']], scan[~data['scope']]):
        raise ValueError('The screen cleanup failed its ink-preservation check. Original page kept.')
    count = int((result != scan).sum())
    details.update(matched=True, changed=count, uncertain=True,
                   protected=int((scan & ~data['mask']).sum()),
                   reconstructed=int((result & ~scan).sum()), reviewed_word_repairs=repairs,
                   stroke_reconstruction=True,
                   pattern_note='Dotted Un-Official mark removed and damaged strokes reconstructed. Surviving document ink is preserved. Review text and signatures before using this cleaned page; reconstruction is approximate.')
    return result, details


def image_stream(band):
    image = DecodedStreamObject()
    image.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                  NameObject('/ColorSpace'): NameObject('/DeviceGray'), NameObject('/BitsPerComponent'): NumberObject(1),
                  NameObject('/Width'): NumberObject(band.shape[1]), NameObject('/Height'): NumberObject(band.shape[0])})
    image.set_data(np.packbits(~band, axis=1).tobytes())
    return image.flate_encode()


def prepare(source, index, directory, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    found = scan_objects(page)
    details = unchanged_details()
    pixels = native_pixels(page, found) if found else None
    result, details = clean_pixels(pixels, cancelled) if pixels is not None else (None, details)
    writer = PdfWriter()
    writer.add_page(page)
    if details['matched']:
        for (name, _), band in zip(found, np.vsplit(result, len(found))):
            replace_image(writer, writer.pages[0], name, image_stream(band))
    temporary = target.with_suffix('.part')
    try:
        if cancelled():
            raise InterruptedError('Cancelled')
        with temporary.open('wb') as output:
            writer.write(output)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
        metadata.write_text(json.dumps(details))
    finally:
        temporary.unlink(missing_ok=True)
    return target, details


def write_clean(source, target, keep, directory, cancelled=lambda: False):
    writer = PdfWriter(clone_from=read_pdf(source))
    for index in keep:
        if cancelled():
            raise InterruptedError('Cancelled')
        cached = Path(directory)/f'{index}-{CACHE}.pdf'
        details = json.loads(cached.with_suffix('.json').read_text())
        if not details['matched']:
            continue
        bands = scan_objects(read_pdf(cached).pages[0])
        original = scan_objects(writer.pages[index])
        if bands is None or original is None or [n for n, _ in bands] != [n for n, _ in original]:
            raise ValueError('The dotted scan cache is invalid. Process the file again.')
        for name, obj in bands:
            replace_image(writer, writer.pages[index], name, obj.clone(writer))
    temporary = Path(target).with_suffix('.part')
    try:
        if cancelled():
            raise InterruptedError('Cancelled')
        with temporary.open('wb') as output:
            writer.write(output)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
