"""Henry County's diagonal red stamp on native 750 x 978 JPEG scans."""
from functools import lru_cache
from io import BytesIO
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
from tempfile import NamedTemporaryFile
import zlib

import cv2
import numpy as np
import pikepdf
from PIL import Image

from clearpage.presets.hocking_outline import replace_resource

METHODS = ('henry_red',)
SIZE = (750, 978)


@lru_cache(maxsize=1)
def template():
    with Image.open(RESOURCE_ROOT/'static/type-henry-red.webp') as image:
        green = np.asarray(image.convert('RGB'))[:, :, 1]
    outline = cv2.resize((green < 250).astype('uint8'), SIZE)
    return green < 233, cv2.dilate(outline, np.ones((19, 19), 'uint8')).astype(bool)


def matching_image(page):
    objects = page.Resources.get('/XObject', {})
    if len(objects) != 1:
        return None
    name, obj = next(iter(objects.items()))
    if (obj.get('/Subtype') != '/Image' or obj.get('/Filter') != '/DCTDecode'
            or (obj.get('/Width'), obj.get('/Height')) != SIZE or obj.get('/BitsPerComponent') != 8
            or any(key in obj for key in ('/Mask', '/SMask', '/Decode', '/DecodeParms', '/ImageMask'))):
        return None
    color = obj.get('/ColorSpace')
    if color != '/DeviceRGB' and not (isinstance(color, pikepdf.Array) and len(color) == 2
            and color[0] == '/ICCBased' and color[1].get('/N') == 3):
        return None
    draws = [str(args[0]) for args, op in pikepdf.parse_content_stream(page) if str(op) == 'Do']
    if draws != [name]:
        return None
    # Read the JPEG directly. Some recorder PDFs contain an unreadable ICC profile.
    with Image.open(BytesIO(obj.read_raw_bytes())) as image:
        if image.size != SIZE or image.mode != 'RGB':
            return None
        rgb = np.array(image)
    red = rgb[:, :, 0].astype('int16') - rgb[:, :, 1]
    expected, _ = template()
    observed = cv2.resize((red > 16).astype('float32'), expected.shape[::-1],
                          interpolation=cv2.INTER_AREA) > .28
    for mark, sample in zip(np.array_split(expected, 4), np.array_split(observed, 4)):
        if np.count_nonzero(mark & sample) < .78 * np.count_nonzero(mark | sample):
            return None
    return name, rgb


def thumbnail(rgb):
    return cv2.resize(rgb[:, :, 1], (150, 196), interpolation=cv2.INTER_AREA).astype('int16')


def suggested(source, index):
    with pikepdf.open(source) as doc:
        if len(doc.pages) < 2:
            return False
        current = matching_image(doc.pages[index])
        if current is None:
            return False
        first = thumbnail(current[1])
        for other in sorted({0, index-1, index+1, len(doc.pages)//2, len(doc.pages)-1}):
            if 0 <= other < len(doc.pages) and other != index:
                peer = matching_image(doc.pages[other])
                if peer is not None and np.abs(thumbnail(peer[1]) - first).mean() >= 1:
                    return True
        return False


def build_reference(scans):
    green = np.stack([rgb[:, :, 1] for rgb in scans]).astype('float32')
    red = np.stack([rgb[:, :, 0] for rgb in scans]).astype('float32')
    chroma = np.median(red - green, axis=0)
    core = (chroma > 8) & template()[1]
    band = cv2.dilate(core.astype('uint8'), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (23, 23)))
    # Estimate paper at each stroke from the clearest surrounding patch.
    paper = 255 - np.clip(chroma, 0, 68)
    cost = np.stack([cv2.boxFilter(np.maximum(paper - page - 15, 0), -1, (11, 11)) for page in green])
    weight = 1 / (.01 + cost)**2
    estimate = np.sum(weight * green, axis=0) / weight.sum(axis=0)
    background = np.where(cost.min(axis=0) > 4, np.maximum(estimate, paper - 12), estimate)
    return band.astype(bool), background


def reference(doc, directory, cancelled):
    cache = Path(directory)/'henry-red-reference-v1.npz'
    if cache.exists():
        with np.load(cache, allow_pickle=False) as data:
            return (data['band'], data['background']) if 'band' in data else None
    scans, thumbnails = [], []
    for page in doc.pages:
        if cancelled():
            raise InterruptedError('Cancelled')
        found = matching_image(page)
        if found is None:
            continue
        rgb = found[1]
        small = thumbnail(rgb)
        if any(np.abs(small - other).mean() < 1 for other in thumbnails):
            continue
        scans.append(rgb)
        thumbnails.append(small)
        if len(scans) == 4:
            break
    model = build_reference(scans) if len(scans) >= 2 else None
    with NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with temporary.open('wb') as handle:
            np.savez_compressed(handle, **(dict(zip(('band','background'), model)) if model else {}))
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return model


def apply_page(doc, page, found, model):
    details = dict(scan_pattern=True, matched=False, changed=0, protected=0, confidence=None,
                   reviewed=False, uncertain=False, pattern_note='No matching Henry County stamp on this page.')
    if found is None:
        return details
    if model is None:
        details['pattern_note'] = 'This cleanup needs two distinct pages with the same Henry County stamp.'
        return details
    name, rgb = found
    band, background = model
    green = rgb[:, :, 1].astype('float32')
    gray = np.rint(255 * np.clip(green / np.maximum(background, 1), 0, 1)).astype('uint8')
    delta = background - green
    near_ink = cv2.dilate((delta > 22).astype('uint8'), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    gray[(delta < 1) | ((delta < 16) & (near_ink == 0))] = 255
    out = rgb.copy()
    out[band] = gray[band, None]
    image = doc.make_stream(zlib.compress(out.tobytes()))
    original = page.Resources.XObject[name]
    for key, value in original.items():
        if key not in ('/Length', '/Filter', '/ColorSpace', '/DecodeParms'):
            image[key] = value
    image.Filter = pikepdf.Name.FlateDecode
    image.ColorSpace = pikepdf.Name.DeviceRGB
    replace_resource(page, name, image)
    details.update(matched=True, uncertain=True, changed=int(np.any(out != rgb, axis=2).sum()),
                   pattern_note='Removed the red diagonal stamp. Review text and signatures at crossings.')
    return details


def save(doc, target, cancelled):
    target = Path(target)
    with NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        doc.save(temporary)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(source, index, directory, cancelled=lambda: False):
    target = Path(directory)/f'{index}-henry-red.pdf'
    with pikepdf.open(source) as original, pikepdf.new() as preview:
        preview.pages.append(original.pages[index])
        page = preview.pages[0]
        found = matching_image(page)
        model = reference(original, directory, cancelled) if found else None
        details = apply_page(preview, page, found, model)
        save(preview, target, cancelled)
    return target, details


def write_clean(source, target, keep, directory, cancelled=lambda: False):
    with pikepdf.open(source) as doc:
        model = reference(doc, directory, cancelled)
        for index in keep:
            if cancelled():
                raise InterruptedError('Cancelled')
            page = doc.pages[index]
            apply_page(doc, page, matching_image(page), model)
        save(doc, target, cancelled)
