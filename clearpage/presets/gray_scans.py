"""Lossless native-pixel export for simple full-page RGB gray-watermark scans."""
from pathlib import Path
import json
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets.dotted_scans import replace_image
from clearpage.presets import scan_patterns

CACHE = 'native-gray-v5-aligned'


def scan_object(page):
    resources = page['/Resources'] if '/Resources' in page else {}
    objects = resources['/XObject'] if '/XObject' in resources else {}
    if len(objects) != 1 or page.rotation or page.get('/UserUnit', 1) != 1:
        return None
    if list(page.cropbox) != list(page.mediabox) or list(page.cropbox)[:2] != [0, 0]:
        return None
    name = next(iter(objects)); obj = objects[name].get_object()
    if obj.get('/Subtype') != '/Image' or obj.get('/BitsPerComponent') != 8 or obj.get('/ColorSpace') != '/DeviceRGB':
        return None
    w, h = int(obj.get('/Width', 0)), int(obj.get('/Height', 0))
    if min(w, h) < 100 or w*h > scan_patterns.MAX_PIXELS:
        return None
    if any(k in obj for k in ('/Mask', '/ImageMask', '/Decode')):
        return None
    if '/SMask' in obj:
        alpha = obj['/SMask'].get_object()
        if (alpha.get('/Width'), alpha.get('/Height'), alpha.get('/BitsPerComponent'), alpha.get('/ColorSpace')) != (w, h, 8, '/DeviceGray'):
            return None
        data = alpha.get_data()
        if len(data) != w*h or any(value != 255 for value in data):
            return None
    content = page.get_contents()
    if content is None: return None
    ops = [(values, op) for values, op in content.operations if op not in (b'q', b'Q')]
    if not ops or ops[-1] != ([name], b'Do'): return None
    matrices = []
    for values, op in ops[:-1]:
        # ReportLab's harmless empty text setup is accepted as well.
        if op in (b'BT', b'ET', b'Tf', b'TL'): continue
        if op != b'cm': return None
        matrices.append([float(v) for v in values])
    if not matrices or any(m != [1, 0, 0, 1, 0, 0] for m in matrices[:-1]): return None
    if not np.allclose(matrices[-1], [float(page.cropbox.width), 0, 0, float(page.cropbox.height), 0, 0], rtol=0, atol=.01):
        return None
    return name, obj


def prepare(source, index, directory, assets, cancelled=lambda: False):
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    if cancelled(): raise InterruptedError('Cancelled')
    page = read_pdf(source).pages[index]
    found = scan_object(page)
    if found is None: return None
    name, obj = found
    a = np.array(page.images[0].image.convert('RGB'))
    if a.shape != (int(obj['/Height']), int(obj['/Width']), 3):
        raise ValueError('The decoded scan dimensions do not match the PDF.')
    result, changed, details = scan_patterns.unofficial_copy(Image.fromarray(a), assets, cancelled)
    writer = PdfWriter(); writer.add_page(page)
    if changed.any():
        cloned = writer.pages[0]['/Resources']['/XObject'][name].get_object()
        image = DecodedStreamObject()
        image.update({k: v for k, v in cloned.items() if k not in ('/Filter', '/DecodeParms', '/Length')})
        image.set_data(result.tobytes())
        replace_image(writer, writer.pages[0], name, image.flate_encode())
    details['native_gray'] = True
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
    writer = PdfWriter(); writer.clone_document_from_reader(read_pdf(source))
    for index in keep:
        if cancelled(): raise InterruptedError('Cancelled')
        path = Path(directory)/f'{index}-{CACHE}.pdf'
        found = scan_object(read_pdf(path).pages[0])
        if found is None: raise ValueError('The native gray scan cache is invalid. Process again.')
        name, obj = found
        replace_image(writer, writer.pages[index], name, obj.clone(writer))
    with open(target, 'wb') as output: writer.write(output)
