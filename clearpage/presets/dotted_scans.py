"""Native-resolution cleanup for two explicitly selected dotted scan layouts.

Only isolated 1-4 pixel dots in a dense diagonal band are removed. Connected
strokes are retained. This is not a generic watermark or text recognizer.
"""
from pathlib import Path
import json
import warnings
import shutil
import cv2
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets import search_package_dots

METHODS = ('dotted_document', 'dotted_map')
MAX_NATIVE_PIXELS = 150_000_000
MAX_REGION_PIXELS = 24_000_000


def image_filter(obj):
    value = obj.get('/Filter')
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def scan_object(page):
    """Accept a single, full-page, bilevel scan with a known palette only."""
    resources = page['/Resources'] if '/Resources' in page else {}
    objects = resources['/XObject'] if '/XObject' in resources else {}
    if len(objects) != 1 or page.rotation or page.get('/UserUnit', 1) != 1:
        return None
    name = next(iter(objects))
    obj = objects[name].get_object()
    w, h = int(obj.get('/Width', 0)), int(obj.get('/Height', 0))
    if not (100 <= w <= 20000 and 100 <= h <= 20000 and w*h <= MAX_NATIVE_PIXELS):
        return None
    if obj.get('/Subtype') != '/Image' or obj.get('/BitsPerComponent') != 1:
        return None
    if any(k in obj for k in ('/Mask', '/SMask', '/ImageMask')):
        return None
    if list(obj.get('/Decode', [0, 1])) != [0, 1]:
        return None
    color = obj['/ColorSpace'] if '/ColorSpace' in obj else None
    if not (isinstance(color, list) and len(color) == 4 and
            color[:3] == ['/Indexed', '/DeviceRGB', 1] and
            isinstance(color[3], bytes) and bytes(color[3]) == b'\xff\xff\xff\x00\x00\x00'):
        return None
    if image_filter(obj) not in ('/CCITTFaxDecode', '/FlateDecode'):
        return None
    if list(page.cropbox) != list(page.mediabox) or list(page.cropbox)[:2] != [0, 0]:
        return None
    # Require the exact simple scan placement, rather than guessing through
    # clipping paths, repeated images, text, or nested form transformations.
    contents = page.get_contents()
    if contents is None:
        return None
    operations = [(v, op) for v, op in contents.operations if op not in (b'q', b'Q')]
    matrices = []
    for values, op in operations[:-1]:
        if op != b'cm':
            return None
        matrices.append([float(v) for v in values])
    if not operations or operations[-1] != ([name], b'Do') or not matrices:
        return None
    if any(m != [1, 0, 0, 1, 0, 0] for m in matrices[:-1]):
        return None
    expected = [float(page.cropbox.width), 0, 0, float(page.cropbox.height), 0, 0]
    if not np.allclose(matrices[-1], expected, rtol=0, atol=.01):
        return None
    return name, obj


def native_pixels(page, obj, rendered):
    w, h = int(obj['/Width']), int(obj['/Height'])
    if image_filter(obj) == '/FlateDecode':
        data = obj.get_data()
        stride = (w+7)//8
        if len(data) != stride*h:
            raise ValueError('The scan image has invalid pixel data.')
        a = 255 - np.unpackbits(np.frombuffer(data, 'uint8').reshape(h, stride), axis=1)[:, :w]*255
    else:
        # Some CCITT Indexed images are exposed with inverted colors by pypdf.
        # Determine polarity against an independent PDF renderer, not a filename.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            a = np.array(page.images[0].image.convert('L'))
        if a.shape != (h, w):
            raise ValueError('The decoded scan dimensions do not match the PDF.')
    reference = rendered.convert('L').resize((240, 240))
    small = np.array(Image.fromarray(a).resize((240, 240)), dtype='float32')
    ref = np.array(reference, dtype='float32')
    direct, inverse = float(np.abs(small-ref).mean()), float(np.abs(255-small-ref).mean())
    if min(direct, inverse) > 25 or abs(direct-inverse) < 40:
        raise ValueError('Could not verify scan colors. This page was not changed.')
    if inverse < direct:
        a = 255-a
    return a


def clean_pixels(a, method):
    if method not in METHODS:
        raise ValueError('Unknown dotted scan layout.')
    h, w = a.shape
    if method == 'dotted_map':
        x0, y0, x1, y1 = [round(v) for v in (w*4400/12920, h*2300/9000, w*8500/12920, h*7100/9000)]
    else:
        x0, y0, x1, y1 = 0, 0, w, h
    crop = a[y0:y1, x0:x1]
    if crop.size > MAX_REGION_PIXELS:
        raise ValueError('This scan exceeds the native dotted-cleanup size limit. The original is unchanged.')
    _, labels, stats, _ = cv2.connectedComponentsWithStats((crop < 128).astype('uint8'), connectivity=4)
    tiny = (stats[:, 4] <= 4) & (stats[:, 2] <= 3) & (stats[:, 3] <= 3)
    tiny[0] = False
    # Never treat a clipped part of a larger stroke as an isolated dot.
    boundary = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    tiny[boundary] = False
    dots = tiny[labels]
    del labels, stats
    density = cv2.boxFilter(dots.astype('float32'), -1, (15, 15), normalize=True)
    y, x = np.ogrid[y0:y1, x0:x1]
    if method == 'dotted_map':
        band = np.abs(x*12920/w + .85*y*9000/h - 10500) < 500
        threshold = .10
    else:
        band = (x/w+y/h > .82) & (x/w+y/h < 1.18) & (x/w > .12) & (x/w < .89) & (y/h > .12) & (y/h < .88)
        threshold = .055
    scope = cv2.dilate(((density > threshold) & band).astype('uint8'), np.ones((9, 9), 'uint8')) > 0
    remove = dots & scope
    count = int(remove.sum())
    # Sparse dust and implausibly extensive selections do not qualify.
    if count < 500 or count > a.size*.025:
        return a, 0
    result = a.copy()
    result[y0:y1, x0:x1][remove] = 255
    return result, count


def replace_image(writer, page, name, image):
    resources = DictionaryObject(dict(page['/Resources']))
    objects = DictionaryObject(dict(resources['/XObject']))
    objects[NameObject(name)] = writer._add_object(image)
    resources[NameObject('/XObject')] = objects
    page[NameObject('/Resources')] = resources


def prepare(source, index, method, directory, rendered, cancelled=lambda: False):
    """Cache a native, lossless single-page PDF; previews never become exports."""
    target = Path(directory)/f'{index}-{method}.pdf'
    metadata = target.with_suffix('.json')
    if cancelled():
        raise InterruptedError('Cancelled')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    if method == 'dotted_document':
        # This screen also removes stroke detail. Isolated-dot deletion cannot
        # repair it; route only a verified match to its native reconstruction.
        from clearpage.presets import screened_scans
        bands = screened_scans.scan_objects(page)
        if bands and screened_scans.matches(screened_scans.native_pixels(page, bands)):
            cached, details = screened_scans.prepare(source, index, directory, cancelled)
            temporary = target.with_suffix('.part')
            try:
                shutil.copyfile(cached, temporary)
                if cancelled():
                    raise InterruptedError('Cancelled')
                temporary.replace(target)
                metadata.write_text(json.dumps(details))
            finally:
                temporary.unlink(missing_ok=True)
            return target, details
    found = scan_object(page)
    details = dict(native_dots=True, changed=0, matched=False, confidence=None,
                   protected=0, reviewed=False, uncertain=False,
                   note='No supported dotted scan found. Original page retained.')
    writer = PdfWriter()
    writer.add_page(page)
    package_scan = search_package_dots.scan_object(page) if method == 'dotted_document' else None
    if package_scan:
        pixels = search_package_dots.native_gray(page, package_scan)
        if search_package_dots.variant(pixels) >= 0:
            template = search_package_dots.template_for(source, index, directory, cancelled)
            if template is None:
                details['pattern_note'] = 'Original page kept. This dotted scan needs more matching reference pages in the uploaded package.'
            else:
                result = search_package_dots.restore(pixels, template)
                count = int(np.count_nonzero(result != pixels))
                if count:
                    name, _ = package_scan
                    cloned = writer.pages[0]['/Resources']['/XObject'][name].get_object()
                    replace_image(writer, writer.pages[0], name, search_package_dots.image_stream(cloned, result))
                    details.update(native_search_package=True, changed=count, matched=True, uncertain=True,
                                   stroke_reconstruction=True,
                                   pattern_note='Dotted watermark removed at scan resolution. Review text and signatures where the mark crossed them.')
    elif found:
        name, obj = found
        a = native_pixels(page, obj, rendered)
        result, count = clean_pixels(a, method)
        if count:
            image = DecodedStreamObject()
            # Use the cloned page's resources: source color spaces may be
            # indirect objects belonging to a different PDF object table.
            cloned = writer.pages[0]['/Resources']['/XObject'][name].get_object()
            image.update({k: v for k, v in cloned.items() if k not in ('/Filter', '/DecodeParms', '/Length')})
            image.set_data(np.packbits(result < 128, axis=1).tobytes())
            replace_image(writer, writer.pages[0], name, image.flate_encode())
            details.update(changed=count, matched=True, uncertain=True,
                           note='Isolated watermark dots removed at original scan resolution. Check small punctuation and map symbols before selecting this page.')
    if cancelled():
        raise InterruptedError('Cancelled')
    temporary = target.with_suffix('.part')
    try:
        with temporary.open('wb') as output:
            writer.write(output)
        temporary.replace(target)
        metadata.write_text(json.dumps(details))
    finally:
        temporary.unlink(missing_ok=True)
    return target, details


def write_clean(source, target, keep, method, directory, cancelled=lambda: False):
    writer = PdfWriter()
    writer.clone_document_from_reader(read_pdf(source))
    package_changed = False
    for index in keep:
        if cancelled():
            raise InterruptedError('Cancelled')
        path = Path(directory)/f'{index}-{method}.pdf'
        details = json.loads(path.with_suffix('.json').read_text())
        if not details['changed']:
            continue
        if details.get('native_screened'):
            from clearpage.presets import screened_scans
            bands = screened_scans.scan_objects(read_pdf(path).pages[0])
            original = screened_scans.scan_objects(writer.pages[index])
            if bands is None or original is None or [n for n, _ in bands] != [n for n, _ in original]:
                raise ValueError('The native screen cache is invalid. Process this file again.')
            for name, obj in bands:
                replace_image(writer, writer.pages[index], name, obj.clone(writer))
            continue
        if details.get('native_search_package'):
            cached = search_package_dots.scan_object(read_pdf(path).pages[0])
            original = search_package_dots.scan_object(writer.pages[index])
            if cached is None or original is None or cached[0] != original[0]:
                raise ValueError('The dotted search-package cache is invalid. Process this file again.')
            name, obj = cached
            replace_image(writer, writer.pages[index], name, obj.clone(writer))
            package_changed = True
            continue
        found = scan_object(read_pdf(path).pages[0])
        if found is None:
            raise ValueError('The native scan cache is invalid. Process this file again.')
        name, obj = found
        replace_image(writer, writer.pages[index], name, obj.clone(writer))
    if package_changed:
        writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
    temporary = Path(target).with_suffix('.part')
    try:
        with temporary.open('wb') as output:
            writer.write(output)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
