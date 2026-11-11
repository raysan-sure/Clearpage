"""Native bilevel Preble County UNOFFICIAL COPY halftone cleanup.

The supplied glyph silhouette gates removal; thick ink and long rules are
retained. No OCR, generated wording, or full-page resampling is used in export.
"""
from pathlib import Path
import json
import cv2
import numpy as np
import native_scan_profile
import preble_resampled
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
from pdf_overlays import read_pdf
from dotted_scans import replace_image, image_filter

METHODS = ('preble_unofficial',)
MAX_PIXELS = 15_000_000
CACHE = 'preble-v5'


def scan_object(page):
    resources = page['/Resources'] if '/Resources' in page else {}
    objects = resources['/XObject'] if '/XObject' in resources else {}
    if len(objects) != 1 or page.rotation or page.get('/UserUnit', 1) != 1:
        return None
    if list(page.cropbox) != list(page.mediabox) or list(page.cropbox)[:2] != [0, 0]:
        return None
    name = next(iter(objects)); obj = objects[name].get_object()
    w, h = int(obj.get('/Width', 0)), int(obj.get('/Height', 0))
    if (obj.get('/Subtype') != '/Image' or obj.get('/BitsPerComponent') != 1
            or obj.get('/ColorSpace') != '/DeviceGray'
            or not 2300 <= w <= 2800 or not 1.2 <= h/w <= 1.8
            or w*h > MAX_PIXELS or image_filter(obj) not in ('/CCITTFaxDecode', '/FlateDecode')
            or any(k in obj for k in ('/Mask', '/SMask', '/ImageMask'))
            or list(obj.get('/Decode', [0, 1])) != [0, 1]):
        return None
    content = page.get_contents()
    if content is None: return None
    ops = content.operations
    depth = 0
    for _, op in ops:
        if op == b'q': depth += 1
        if op == b'Q': depth -= 1
        if depth < 0: return None
    if depth: return None
    ops = [(v, op) for v, op in ops if op not in (b'q', b'Q')]
    if len(ops) != 2 or ops[0][1] != b'cm' or ops[1] != ([name], b'Do'):
        return None
    m = [float(v) for v in ops[0][0]]
    pw, ph = float(page.cropbox.width), float(page.cropbox.height)
    if (len(m) != 6 or m[1:3] != [0, 0] or m[0] <= 0 or m[3] <= 0
            or not 0 <= m[5] <= ph*.25 or abs(m[3]+m[5]-ph) > .02
            or not 0 <= m[4] <= pw*.025 or abs(m[0]+2*m[4]-pw) > .02
            or abs(m[0]/m[3]-w/h) > .005):
        return None
    return name, obj


def ink_guard(ink):
    """Retain solid strokes without keeping a halo of adjacent halftone ink.

    Opposite erosion/dilation anchors avoid the one-pixel translation caused
    by OpenCV's default even-kernel opening. Short horizontal serifs qualify
    only next to solid ink; long rules are preserved independently.
    """
    kernel = np.ones((2, 2), 'uint8')
    solid = cv2.dilate(cv2.erode(ink, kernel, anchor=(0, 0)),
                       kernel, anchor=(1, 1))
    for rule in (np.ones((1, 101), 'uint8'), np.ones((101, 1), 'uint8')):
        solid |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, rule)
    near = cv2.dilate(solid, np.ones((3, 15), 'uint8')) > 0
    serifs = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, 9), 'uint8'))
    return (solid > 0) | ((serifs > 0) & near)


def clean_pixels(a, assets, cancelled=lambda: False):
    h, w = a.shape
    details = dict(native_preble=True, changed=0, matched=False, confidence=None,
                   protected=0, reviewed=False, uncertain=False,
                   note='No matching Preble County halftone watermark. Original retained.')
    if not 2300 <= w <= 2800 or not 1.2 <= h/w <= 1.8 or a.size > MAX_PIXELS:
        return a, details
    with np.load(Path(assets)/'preble-unofficial.npz', allow_pickle=False) as data:
        mask = data['mask']
    small = cv2.resize(a, (768, round(h*768/w)), interpolation=cv2.INTER_AREA)[:580]
    template = mask[80:540, 100:570].astype('float32')
    density = np.clip((255-small.astype('float32'))/100, 0, 1)
    scores = cv2.matchTemplate(density[55:565, 75:595], template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (dx, dy) = cv2.minMaxLoc(scores)
    details['confidence'] = round(score, 3)
    if score < .30: return a, details
    scope = np.zeros_like(mask)
    scope[55+dy:515+dy, 75+dx:545+dx] = template
    scope = cv2.dilate(scope, np.ones((15, 15), 'uint8'))
    scope = cv2.resize(scope, (w, round(580*w/768)), interpolation=cv2.INTER_NEAREST)
    scope = np.pad(scope, ((0, h-scope.shape[0]), (0, 0))).astype(bool)
    ink = (a < 128).astype('uint8')
    protected = ink_guard(ink)
    remove = scope & ~protected & (ink > 0)
    count = int(remove.sum())
    # Require substantial fine-line ink as well as the glyph appearance.
    fraction = count/max(1, int((scope & (ink > 0)).sum()))
    if count < 12000 or fraction < .20 or count > a.size*.012:
        return a, details
    if cancelled(): raise InterruptedError('Cancelled')
    result = a.copy(); result[remove] = 255
    details.update(changed=count, matched=True, uncertain=True,
                   protected=int((scope & protected & (ink > 0)).sum()),
                   note='Preble County fine-line watermark and attached edge fringe cleaned at original resolution. Solid strokes, supported serifs and long rules retained; inspect fine text and intersections for residual marks.')
    return result, details


def prepare(source, index, directory, assets, cancelled=lambda: False):
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if cancelled(): raise InterruptedError('Cancelled')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    found = scan_object(page)
    if found is None:
        return native_scan_profile.prepare(source, index, directory, CACHE, preble_resampled.process, cancelled)
    details = dict(native_preble=True, changed=0, matched=False, confidence=None,
                   protected=0, reviewed=False, uncertain=False,
                   note='Unsupported Preble County scan structure. Original retained.')
    writer = PdfWriter(); writer.add_page(page)
    if found:
        name, obj = found
        a = np.array(page.images[0].image.convert('L'))
        if a.shape != (int(obj['/Height']), int(obj['/Width'])):
            raise ValueError('The decoded scan dimensions do not match the PDF.')
        result, details = clean_pixels(a, assets, cancelled)
        if details['changed']:
            cloned = writer.pages[0]['/Resources']['/XObject'][name].get_object()
            image = DecodedStreamObject()
            image.update({k: v for k, v in cloned.items() if k not in ('/Filter', '/DecodeParms', '/Length')})
            image.set_data(np.packbits(result >= 128, axis=1).tobytes())
            replace_image(writer, writer.pages[0], name, image.flate_encode())
    if cancelled(): raise InterruptedError('Cancelled')
    temporary = target.with_suffix('.part')
    try:
        with temporary.open('wb') as output: writer.write(output)
        temporary.replace(target)
        metadata.write_text(json.dumps(details))
    finally:
        temporary.unlink(missing_ok=True)
    return target, details


def write_clean(source, target, keep, directory, cancelled=lambda: False):
    native_scan_profile.export_cached(source, target, keep, directory, CACHE, cancelled)
