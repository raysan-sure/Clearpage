"""Native-pixel cleanup for the large gray diagonal NOT VALID scan watermark."""
from functools import lru_cache
import json
from pathlib import Path
import zlib

import cv2
import numpy as np
import pikepdf
from PIL import Image

from clearpage.paths import RESOURCE_ROOT
from clearpage.presets.native_scan_profile import image_entry
from clearpage.presets.hocking_outline import replace_resource
from clearpage.presets.henry_red import save

METHODS = ('not_valid',)
CACHE = 'not-valid-v1'
SIZE = (1272, 1682)
SAMPLE = (636, 841)


@lru_cache(maxsize=1)
def pattern():
    with np.load(RESOURCE_ROOT/'assets/not-valid.npz', allow_pickle=False) as data:
        if tuple(data['size']) != SIZE:
            raise ValueError('The NOT VALID watermark profile is invalid.')
        mask = np.unpackbits(data['mask'], axis=1)[:, :SIZE[0]]
    if mask.shape != SIZE[::-1]:
        raise ValueError('The NOT VALID watermark profile is invalid.')
    reference = cv2.GaussianBlur(cv2.resize(mask.astype('float32'), SAMPLE), (5, 5), 0)
    return mask, reference


def scan(page):
    found = image_entry(page, 8, {'/DeviceRGB', '/DeviceGray'}, {'/DCTDecode', '/FlateDecode'},
                        lambda w, h: 800 <= w <= 1600 and 1100 <= h <= 2700,
                        allow_opaque_mask=True)
    if found is None:
        return None
    name, obj = found
    if '/OC' in obj or '/Group' in page:
        return None
    return name, np.array(pikepdf.PdfImage(obj).as_pil_image().convert('RGB'))


def locate(rgb, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    gray = rgb.min(axis=2)
    height, width = gray.shape
    observed = cv2.resize(gray, SIZE, interpolation=cv2.INTER_LINEAR)
    observed = cv2.morphologyEx(((observed > 115) & (observed < 210)).astype('uint8'),
                               cv2.MORPH_OPEN, np.ones((3, 3), 'uint8'))
    if observed.sum() < 5000 or np.mean(gray > 225) < .4:
        return None
    mask, reference = pattern()
    field = cv2.GaussianBlur(cv2.resize(observed.astype('float32'), SAMPLE), (5, 5), 0)
    expected = np.array([width/SIZE[0], height/SIZE[1]], dtype='float32')
    warp = np.array([[expected[0], 0, 0], [0, expected[1], 0]], dtype='float32')
    try:
        score, warp = cv2.findTransformECC(field, reference, warp, cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
    except cv2.error:
        return None
    if cancelled():
        raise InterruptedError('Cancelled')
    relative = warp[:, :2]/expected[:, None]
    if (not np.isfinite(warp).all() or not np.isfinite(score) or score < .88
            or np.max(np.abs(np.diag(relative)-1)) > .08
            or abs(relative[0, 1]) > .035 or abs(relative[1, 0]) > .035
            or np.max(np.abs(warp[:, 2])) > 10):
        return None
    warp[:, 2] *= 2
    aligned = cv2.warpAffine(mask, warp, SIZE, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    aligned = cv2.resize(aligned, (width, height), interpolation=cv2.INTER_NEAREST)
    core = aligned > 0
    # Require gray lettering throughout the visible phrase, rather than a match
    # to a single unrelated gray shape or a dark document heading.
    coverage = []
    for band in np.array_split(np.arange(height), 8):
        ink = core[band]
        if ink.sum() >= 500:
            values = gray[band][ink]
            coverage.append(np.mean((values > 115) & (values < 210)))
    if len(coverage) < 4 or np.count_nonzero(np.asarray(coverage) >= .45) < len(coverage)-1:
        return None
    scope = cv2.dilate(aligned, np.ones((7, 7), 'uint8')) > 0
    return scope, float(score)


def clean_pixels(rgb, cancelled=lambda: False):
    details = dict(scan_pattern=True, matched=False, changed=0, protected=0,
                   confidence=None, reviewed=False, uncertain=False, ai=False,
                   pattern_note='No matching gray NOT VALID stamp detected. Original page retained.')
    located = locate(rgb, cancelled)
    if located is None:
        return rgb.copy(), details
    scope, score = located
    gray = rgb.min(axis=2)
    guard = cv2.dilate((gray < 145).astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    neutral = np.ptp(rgb, axis=2) <= 12
    result = rgb.copy()
    result[scope & ~guard & neutral] = 255
    edge = scope & guard & (gray > 75) & neutral
    result[edge] = np.minimum(255, np.round(rgb[edge].astype('float32')*255/162)).astype('uint8')
    if (not np.array_equal(result[gray <= 75], rgb[gray <= 75])
            or not np.array_equal(result[~scope], rgb[~scope])
            or not np.array_equal(result[~neutral], rgb[~neutral])):
        raise ValueError('NOT VALID cleanup failed pixel-preservation checks.')
    changed = int(np.any(result != rgb, axis=2).sum())
    details.update(matched=True, changed=changed, protected=int((scope & (gray <= 75)).sum()),
                   uncertain=bool(changed), pattern_match=round(score, 5),
                   dark_pixels_exact=True, colors_preserved=True, outside_mask_exact=True,
                   pattern_note='Gray NOT VALID lettering removed at the original scan resolution. Dark pixels and colored ink are unchanged. Review faint text and crossings before export.')
    return result, details


def suggested(source, index):
    with pikepdf.open(source) as doc:
        found = scan(doc.pages[index])
        return found is not None and locate(found[1]) is not None


def prepare(source, index, directory, cancelled=lambda: False):
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if cancelled():
        raise InterruptedError('Cancelled')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text(encoding='utf-8'))
    with pikepdf.open(source) as doc, pikepdf.new() as preview:
        preview.pages.append(doc.pages[index])
        found = scan(doc.pages[index])
        if found is None:
            details = dict(scan_pattern=True, matched=False, changed=0, protected=0,
                           confidence=None, reviewed=False, uncertain=False, ai=False,
                           pattern_note='Unsupported scan layout. Original page retained.')
        else:
            name, rgb = found
            result, details = clean_pixels(rgb, cancelled)
            if details['changed']:
                original = preview.pages[0].Resources.XObject[name]
                image = preview.make_stream(zlib.compress(result.tobytes()))
                image.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                    Width=result.shape[1], Height=result.shape[0], BitsPerComponent=8,
                    ColorSpace=pikepdf.Name.DeviceRGB, Filter=pikepdf.Name.FlateDecode))
                if '/Interpolate' in original:
                    image.Interpolate = original.Interpolate
                replace_resource(preview.pages[0], name, image)
        save(preview, target, cancelled)
    metadata.write_text(json.dumps(details), encoding='utf-8')
    return target, details


def write_clean(source, target, keep, directory, cancelled=lambda: False):
    with pikepdf.open(source) as doc:
        for index in keep:
            if cancelled():
                raise InterruptedError('Cancelled')
            cached = Path(directory)/f'{index}-{CACHE}.pdf'
            if not json.loads(cached.with_suffix('.json').read_text(encoding='utf-8'))['changed']:
                continue
            with pikepdf.open(cached) as preview:
                name = next(iter(preview.pages[0].Resources.XObject.keys()))
                original = doc.pages[index].Resources.XObject.get(name)
                image = preview.pages[0].Resources.XObject[name]
                if original is None or (original.Width, original.Height) != (image.Width, image.Height):
                    raise ValueError('The NOT VALID preview no longer matches the source page.')
                replace_resource(doc.pages[index], name, doc.copy_foreign(image))
        oc = doc.Root.get('/OCProperties')
        if oc is not None and set(oc.keys()) == {'/D'} and len(oc.D) == 0:
            del doc.Root['/OCProperties']
        save(doc, target, cancelled)


def display_image(source, index):
    """Use native scan pixels for simple pages; compose annotations/other marks."""
    with pikepdf.open(source) as doc:
        page = doc.pages[index]
        found = scan(page)
        if found is None:
            return None
        name, rgb = found
        left, bottom, right, top = map(float, page.cropbox)
        width, height = right-left, top-bottom
        ops = list(pikepdf.parse_content_stream(page))
        if (not page.get('/Rotate', 0) and not page.get('/Annots')
                and [str(op) for _, op in ops] == ['q', 'cm', 'Do', 'Q']
                and str(ops[2].operands[0]) == name
                and np.allclose(list(map(float, ops[1].operands)),
                                [width, 0, 0, height, left, bottom], rtol=0, atol=.001)):
            return Image.fromarray(rgb)
    from clearpage.pdf.engine import render_page
    return render_page(source, index, scale=max(rgb.shape[1]/width, rgb.shape[0]/height))
