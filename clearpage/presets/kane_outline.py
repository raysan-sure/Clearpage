"""Native-pixel removal of the verified Kane County dotted outline.

The profile contains watermark pixels only. Pixels under the dots are estimated
from their immediate neighbors; no OCR, donor text, or AI is used.
"""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import json
import hashlib
import cv2
import numpy as np
from pypdf import PdfWriter
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets.dotted_scans import replace_image
from clearpage.presets.screened_scans import scan_objects, native_pixels, image_stream

METHODS = ('kane_outline',)
CACHE = 'kane-outline-v1'
SHAPE = (2160, 1728)


@lru_cache(maxsize=1)
def profile():
    path = RESOURCE_ROOT/'assets/kane-outline.npz'
    if hashlib.sha256(path.read_bytes()).hexdigest() != '9e3671d090ade5985e0d66e5ecf07e12420248a5e26e02a57ed550de30bf3b94':
        raise ValueError('The Kane County watermark profile is damaged. Reinstall ClearPage.')
    with np.load(path, allow_pickle=False) as archive:
        mask = np.unpackbits(archive['mask'], axis=1).astype(bool)
    if mask.shape != SHAPE or mask.sum() != 6063:
        raise ValueError('The Kane County watermark profile is damaged. Reinstall ClearPage.')
    ring = cv2.dilate(mask.astype('uint8'), np.ones((5, 5), 'uint8')).astype(bool) & ~mask
    return mask, ring


def matches(scan):
    if scan is None or scan.shape != SHAPE or scan.dtype != np.bool_:
        return False
    mask, ring = profile()
    return bool(scan[mask].all() and scan[ring].mean() < .35)


def unchanged_details():
    return dict(native_kane=True, scan_pattern=True, matched=False, changed=0,
                confidence=None, protected=0, reviewed=False, uncertain=False,
                pattern_note='Original page kept: no matching Kane County dotted outline found.')


def clean_pixels(scan, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    details = unchanged_details()
    if not matches(scan):
        return scan, details
    mask, _ = profile()
    gray = np.where(scan, 0, 255).astype('uint8')
    repaired = cv2.inpaint(gray, mask.astype('uint8'), 3, cv2.INPAINT_TELEA)
    result = scan.copy()
    # Keep the native bilevel output: no pale gray outline can be introduced.
    result[mask] = repaired[mask] < 128
    if cancelled():
        raise InterruptedError('Cancelled')
    if not np.array_equal(result[~mask], scan[~mask]):
        raise ValueError('Ink-preservation check failed. Original page kept.')
    details.update(matched=True, changed=int(np.count_nonzero(result != scan)),
                   protected=int(scan[~mask].sum()), uncertain=True,
                   stroke_reconstruction=True, watermark_pixels=int(mask.sum()),
                   pattern_note='Kane County dotted outline removed at original scan resolution. All pixels outside its dots are unchanged. Review tiny reconstructed intersections with text and signatures.')
    return result, details


def prepare(source, index, directory, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    bands = scan_objects(page)
    scan = native_pixels(page, bands) if bands else None
    result, details = clean_pixels(scan, cancelled)
    writer = PdfWriter()
    writer.add_page(page)
    if details['matched']:
        for (name, _), band in zip(bands, np.vsplit(result, len(bands))):
            replace_image(writer, writer.pages[0], name, image_stream(band))
    temporary = target.with_suffix('.part')
    try:
        if cancelled():
            raise InterruptedError('Cancelled')
        writer.write(temporary)
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
            raise ValueError('The Kane County scan cache is invalid. Process the file again.')
        for name, obj in bands:
            replace_image(writer, writer.pages[index], name, obj.clone(writer))
    temporary = Path(target).with_suffix('.part')
    try:
        if cancelled():
            raise InterruptedError('Cancelled')
        writer.write(temporary)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
