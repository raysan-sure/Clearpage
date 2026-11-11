"""Local red-overlay inversion for explicitly selected diagonal scan watermarks."""
from pathlib import Path
import json
import cv2
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets.dotted_scans import replace_image
from clearpage.presets.gray_scans import scan_object

MAX_PIXELS = 12_000_000
CACHE = 'native-red-v3-cumberland'
CUMBERLAND_CACHE = 'native-cumberland-red-v1'


def clean(image, cancelled=lambda: False):
    if cancelled(): raise InterruptedError('Cancelled')
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('This scan is too large for red-watermark cleanup.')
    from clearpage.presets import cumberland_red
    if cumberland_red.matches(image):
        return cumberland_red.clean(image, cancelled)
    return clean_general(image, cancelled)


def clean_general(image, cancelled=lambda: False):
    if cancelled(): raise InterruptedError('Cancelled')
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('This scan is too large for red-watermark cleanup.')
    a = np.array(image.convert('RGB'))
    h, w = a.shape[:2]
    d = a.astype('int16')
    red = (d[:, :, 0]-d[:, :, 1] > 8) & (d[:, :, 0]-d[:, :, 2] > 8)
    pale = red & (d[:, :, 0] > 180) & (d[:, :, 1] > 90)
    yy, xx = np.where(pale)
    details = dict(scan_pattern=True, confidence=None, matched=False, changed=0,
                   protected=0, reviewed=False, uncertain=False, ai=False,
                   pattern_note='No large diagonal red watermark found. Page kept unchanged.')
    empty = np.zeros((h, w), bool)
    if len(xx) < max(100, h*w*.003): return a, empty, details
    points = np.column_stack((xx, yy)).astype('float32')
    center = np.median(points, axis=0)
    values, vectors = np.linalg.eigh(np.cov(points.T))
    axis = vectors[:, -1]
    angle = abs(np.degrees(np.arctan2(axis[1], axis[0]))) % 180
    angle = min(angle, 180-angle)
    spans = np.percentile(points, 98, axis=0)-np.percentile(points, 2, axis=0)
    if (not 20 <= angle <= 80 or values[-1] < 8*max(values[0], 1)
            or spans[0] < .55*w or spans[1] < .55*h):
        return a, empty, details
    # A separate red seal outside the diagonal band must stay unchanged.
    normal = np.array([-axis[1], axis[0]])
    ygrid, xgrid = np.ogrid[:h, :w]
    band = np.abs((xgrid-center[0])*normal[0]+(ygrid-center[1])*normal[1]) <= .12*min(h,w)
    mask = cv2.dilate((red & band).astype('uint8'), np.ones((9,9),'uint8')) > 0
    if cancelled(): raise InterruptedError('Cancelled')
    r = a[:, :, 0].astype('float32')
    neutral = np.minimum(a[:, :, 1], a[:, :, 2]).astype('float32')
    # For a red overlay over neutral ink: alpha=(R-G)/255, source=G/(1-alpha).
    recovered = np.clip(255*neutral/np.maximum(255-r+neutral, 1), 0, 255)
    # The old contrast ramp also whitened faint letter edges in the expanded
    # mask. Neutral scan pixels are evidence, not watermark: keep them exact.
    chroma = d.max(axis=2)-d.min(axis=2)
    neutral_ink = (chroma <= 8) & (neutral < 250)
    # Infer text rows from source ink outside the watermark neighborhood.
    # Do not grow protection along an entire connected watermark outline.
    anchors = ((neutral < 245) & ~mask).astype('uint8')
    lines = cv2.morphologyEx(anchors, cv2.MORPH_CLOSE, np.ones((1,61),'uint8'))
    _, _, boxes, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    text_rows = np.zeros((h,w),bool)
    for x,y,bw,bh,area in boxes[1:]:
        if bw >= 12 and area >= 6:
            text_rows[max(0,y-2):min(h,y+bh+2),max(0,x-30):min(w,x+bw+30)] = True
    ink_seed = (recovered < 160) | (text_rows & (recovered < 245))
    ink = cv2.dilate(ink_seed.astype('uint8'), np.ones((3,3),'uint8')) > 0
    background = np.minimum(255, 160+(np.maximum(recovered,160)-160)*95/50)
    channel = np.rint(np.where(ink, recovered, background)).astype('uint8')
    result = a.copy()
    result[mask] = np.repeat(channel[:, :, None], 3, axis=2)[mask]
    # Expansion is for context only: never recolor neutral or non-red pixels.
    result[mask & ~red] = a[mask & ~red]
    if cancelled(): raise InterruptedError('Cancelled')
    changed = np.any(result != a, axis=2)
    details.update(matched=True, changed=int(changed.sum()), uncertain=bool(changed.any()),
                   selected_pixels=int(mask.sum()), neutral_ink_pixels_preserved=int((mask & neutral_ink).sum()),
                   ink_protection_version=2,
                   pattern_note='Red watermark cleaned locally with faint-ink protection. Neutral text pixels are retained exactly; contrast whitening is excluded from detected letter strokes and edges. Review overlapping text and signatures; ambiguous traces may remain.')
    return result, changed, details


def prepare(source, index, directory, cancelled=lambda: False, *, cumberland=False):
    if cancelled(): raise InterruptedError('Cancelled')
    cache = CUMBERLAND_CACHE if cumberland else CACHE
    target = Path(directory)/f'{index}-{cache}.pdf'
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    found = scan_object(page)
    if found is None: return None
    name, obj = found
    image = page.images[0].image.convert('RGB')
    if image.size != (int(obj['/Width']), int(obj['/Height'])):
        raise ValueError('The decoded scan dimensions do not match the PDF.')
    if cumberland:
        from clearpage.presets.cumberland_red import clean as refine
        result, changed, details = refine(image, cancelled)
    else:
        result, changed, details = clean(image, cancelled)
    writer = PdfWriter(); writer.add_page(page)
    if changed.any():
        cloned = writer.pages[0]['/Resources']['/XObject'][name].get_object()
        stream = DecodedStreamObject()
        stream.update({k:v for k,v in cloned.items() if k not in ('/Filter','/DecodeParms','/Length')})
        stream.set_data(result.tobytes())
        replace_image(writer, writer.pages[0], name, stream.flate_encode())
    details['native_red'] = True
    temporary = target.with_suffix('.part')
    try:
        if cancelled(): raise InterruptedError('Cancelled')
        with temporary.open('wb') as output: writer.write(output)
        if cancelled(): raise InterruptedError('Cancelled')
        temporary.replace(target)
        metadata.write_text(json.dumps(details))
    finally:
        temporary.unlink(missing_ok=True)
    return target, details


def write_clean(source, target, keep, directory, cancelled=lambda: False, *, cumberland=False):
    cache = CUMBERLAND_CACHE if cumberland else CACHE
    writer = PdfWriter(); writer.clone_document_from_reader(read_pdf(source))
    for index in keep:
        if cancelled(): raise InterruptedError('Cancelled')
        path = Path(directory)/f'{index}-{cache}.pdf'
        found = scan_object(read_pdf(path).pages[0])
        if found is None: raise ValueError('The red scan cache is invalid. Process again.')
        name, obj = found
        replace_image(writer, writer.pages[index], name, obj.clone(writer))
    if cancelled(): raise InterruptedError('Cancelled')
    with open(target, 'wb') as output: writer.write(output)
