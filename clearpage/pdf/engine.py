"""Deterministic, local scan cleanup. No OCR synthesis or external services."""
from pathlib import Path
from io import BytesIO
import math
import hashlib
import cv2
import numpy as np
from PIL import Image
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from clearpage.pdf.pdf_overlays import read_pdf, rewrite_node
from clearpage.presets import dotted_scans

SCALE = 2
MAX_PAGES = 300
MAX_PIXELS = 12_000_000
cv2.setNumThreads(2)


def inspect_pdf(path):
    reader = read_pdf(path)
    count = len(reader.pages)
    if not 1 <= count <= MAX_PAGES:
        raise ValueError(f'Please use a PDF with 1 to {MAX_PAGES} pages.')
    if reader.get_fields():
        raise ValueError('Interactive forms and signed forms are not supported. Use a separate scanned copy.')
    sizes = []
    for page in reader.pages:
        w, h = float(page.cropbox.width), float(page.cropbox.height)
        oversized = not (1 <= w <= 4000 and 1 <= h <= 4000) or w*h*SCALE*SCALE > MAX_PIXELS
        if oversized:
            # Recognized vector overlays can be removed without rasterizing the
            # output. render_page still caps the preview at MAX_PIXELS.
            bounded = 1 <= w <= 20000 and 1 <= h <= 20000
            supported = bounded and (dotted_scans.scan_object(page) or
                rewrite_node(page.get_contents(), page.get('/Resources', {}), reader)[2])
            if bounded and not supported:
                from clearpage.presets import union_parish
                scan = union_parish.scan_object(page)
                if scan and scan[1].get('/Filter') == '/DCTDecode':
                    rgb = np.asarray(page.images[0].image.convert('RGB'))
                    supported = bool(union_parish.stamp_boxes(rgb, jpeg=True))
            if not supported:
                raise ValueError('A page is too large. Maximum rendered page size is 12 million pixels.')
        if page.rotation % 90:
            raise ValueError('This PDF has an unsupported page rotation.')
        sizes.append([w, h])
    return count, sizes


def render_page(path, index):
    # All calls are serialized by the app; PDFium must not run concurrently.
    with pdfium.PdfDocument(str(path)) as document:
        page = document[index]
        w, h = page.get_size()
        scale = min(SCALE, math.sqrt((MAX_PIXELS-20000)/(w*h)))
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert('RGB').copy()
        bitmap.close()
        page.close()
        return image


def known_alignment(image, template_path, min_score=.35):
    """Locate the supplied watermark and retain its scale for inverse blending."""
    template = np.load(template_path, allow_pickle=False)
    rotated = image.convert('L').rotate(-45, resample=Image.Resampling.BICUBIC,
                                        expand=True, fillcolor=255)
    a = np.asarray(rotated)
    ratio = image.height / 1684
    x0, y0 = int(420*ratio), int(600*ratio)
    x1, y1 = min(a.shape[1], int(1900*ratio)), min(a.shape[0], int(940*ratio))
    roi = a[y0:y1, x0:x1]
    best = None
    # Broad coarse search followed by a fine scale search.
    def search(scales):
        nonlocal best
        for scale in scales:
            q = cv2.resize(template, None, fx=float(scale*ratio), fy=float(scale*ratio),
                           interpolation=cv2.INTER_CUBIC)
            if q.shape[0] > roi.shape[0] or q.shape[1] > roi.shape[1]:
                continue
            _, score, _, pos = cv2.minMaxLoc(cv2.matchTemplate(roi, q, cv2.TM_CCOEFF_NORMED))
            if best is None or score > best[0]:
                best = (score, pos[0]+x0, pos[1]+y0, q, scale)
    if roi.size:
        search(np.arange(.70, 1.161, .02))
    if best is None:
        return None, 0.
    search(np.arange(best[4]-.02, best[4]+.021, .005))
    score, x, y, q, _ = best
    if score < min_score:
        return None, float(score)
    return dict(x=x, y=y, template=q, rotated_shape=a.shape), float(score)


def known_mask(image, template_path, min_score=.35):
    """Match only the supplied Huntington watermark, not arbitrary diagonal text."""
    match, score = known_alignment(image, template_path, min_score)
    if match is None:
        return np.zeros((image.height, image.width), bool), score
    x,y,q = match['x'], match['y'], match['template']
    ink = cv2.dilate((q < 237).astype('uint8')*255,
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    canvas = np.zeros(match['rotated_shape'], 'uint8')
    canvas[y:y+q.shape[0], x:x+q.shape[1]] = ink
    back = Image.fromarray(canvas).rotate(45, resample=Image.Resampling.BILINEAR,
                                          expand=False, fillcolor=0)
    left, top = (back.width-image.width)//2, (back.height-image.height)//2
    return np.asarray(back.crop((left, top, left+image.width, top+image.height))) > 40, float(score)


def paint_mask(size, strokes):
    """Normalized strokes are portable across page sizes. Coordinates never become paths."""
    width, height = size
    mask = np.zeros((height, width), 'uint8')
    if not isinstance(strokes, list) or len(strokes) > 300:
        raise ValueError('Too many brush strokes. Clear the selection and try again.')
    for stroke in strokes:
        points = stroke.get('points', [])
        radius = float(stroke.get('width', .025))
        if not math.isfinite(radius) or not .001 <= radius <= .2 or len(points) > 3000:
            raise ValueError('Invalid brush size or stroke.')
        coords = []
        for point in points:
            if len(point) != 2 or any(not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in point):
                raise ValueError('Invalid brush coordinates.')
            coords.append((min(width-1, round(point[0]*width)), min(height-1, round(point[1]*height))))
        thickness = max(1, round(radius*width))
        value = 0 if stroke.get('erase') else 255
        for point in coords:
            cv2.circle(mask, point, max(1, thickness//2), value, -1)
        for start, end in zip(coords, coords[1:]):
            cv2.line(mask, start, end, value, thickness)
    return mask > 0


def clean(image, mask, mode='careful'):
    source = np.asarray(image)
    if not np.any(mask):
        return source.copy(), {'changed': 0, 'protected': 0}
    gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    selected = mask.copy()
    protected = np.zeros(mask.shape, bool)
    # Legacy strong requests now retain text protection. Interpolating through
    # document text damaged letter strokes in the first release.
    if mode in ('careful', 'strong'):
        exclusion = cv2.dilate(mask.astype('uint8'), np.ones((5, 5), 'uint8')) > 0
        ink = ((gray < 170) & ~exclusion).astype('float32')
        support = cv2.boxFilter(ink, -1, (121, 3), normalize=True)
        near = cv2.boxFilter(ink, -1, (41, 3), normalize=True)
        bounds = cv2.morphologyEx((ink > 0).astype('uint8'), cv2.MORPH_CLOSE, np.ones((1, 61), 'uint8'))
        bounds = cv2.dilate(bounds, np.ones((3, 21), 'uint8')) > 0
        lines = ((support > .075) | (near > .065)) & bounds
        protected = cv2.dilate(lines.astype('uint8'), np.ones((5, 5), 'uint8')) > 0
        # Preserve complete dark strokes that extend outside the selection.
        # Include faint connected letter strokes, not only dark print. Entirely
        # covered letters still cannot be identified from these pixels alone.
        _, labels = cv2.connectedComponents((gray < 225).astype('uint8'), connectivity=8)
        touching = np.unique(labels[(~mask) & (gray < 225)])
        touching = touching[touching != 0]
        connected = np.isin(labels, touching)
        protected |= cv2.dilate(connected.astype('uint8'), np.ones((3, 3), 'uint8')) > 0
        selected &= ~protected
    elif mode == 'light':
        # Background-only cleanup protects every dark pixel and its immediate edge.
        protected = cv2.dilate((gray < 180).astype('uint8'), np.ones((5, 5), 'uint8')) > 0
        selected &= ~protected
    else:
        raise ValueError('Unknown cleanup strength.')
    if selected.any():
        result = cv2.inpaint(source, selected.astype('uint8')*255, 3, cv2.INPAINT_TELEA)
        result[~selected] = source[~selected]
    else:
        result = source.copy()
    overlap=mask & protected
    count,labels,boxes,_=cv2.connectedComponentsWithStats(overlap.astype('uint8'),8)
    regions=[]
    h,w=mask.shape
    for x,y,bw,bh,area in boxes[1:]:
        if area>=12:regions.append([round(x/w,5),round(y/h,5),round((x+bw)/w,5),round((y+bh)/h,5)])
    return result, {'changed': int(np.any(result != source, axis=2).sum()),
                    'protected': int(overlap.sum()), 'scan_overlap_regions': regions[:40]}


def page_fingerprint(image):
    rgb = np.asarray(image.convert('RGB'))
    return hashlib.sha256(str(rgb.shape).encode('ascii') + rgb.tobytes()).hexdigest()


def save_reviewed_patch(source, reviewed, allowed, target):
    a, b = np.asarray(source.convert('RGB')), np.asarray(reviewed.convert('RGB'))
    if a.shape != b.shape:
        raise ValueError('The reviewed page has different dimensions.')
    changed = np.any(a != b, axis=2)
    if np.any(changed & ~allowed):
        raise ValueError('The reviewed copy changes content outside the detected watermark area.')
    if changed.sum() > a.shape[0]*a.shape[1]*.10:
        raise ValueError('The reviewed copy changes too much of the original page.')
    indices = np.flatnonzero(changed).astype('uint32')
    if indices.size:
        np.savez_compressed(target, shape=np.array(a.shape), indices=indices,
                            pixels=b.reshape(-1, 3)[indices])
    return int(indices.size)


def load_reviewed_patch(image, directory):
    path = Path(directory)/(page_fingerprint(image)+'.npz')
    if not path.is_file():
        return None
    a = np.array(image.convert('RGB'))
    try:
        with np.load(path, allow_pickle=False) as patch:
            indices, pixels = patch['indices'], patch['pixels']
            if not np.array_equal(patch['shape'], a.shape) or indices.ndim != 1 or pixels.shape != (indices.size, 3):
                return None
            if indices.size > a.shape[0]*a.shape[1]*.10 or np.any(indices >= a.shape[0]*a.shape[1]):
                return None
            a.reshape(-1, 3)[indices] = pixels
        return a
    except (OSError, ValueError, KeyError, IndexError):
        return None


def write_pdf(source_path, output_path, edits, cancelled=lambda: False):
    """Add sparse visible corrections; preserve source objects, page order and geometry.

    This is visual cleanup, not redaction: source pixels remain in underlying objects.
    """
    reader = read_pdf(source_path)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    for index, (before_path, after_path) in edits.items():
        if cancelled():
            raise InterruptedError('Cancelled')
        before = np.asarray(Image.open(before_path).convert('RGB'))
        after = np.asarray(Image.open(after_path).convert('RGB'))
        changed = np.any(before != after, axis=2)
        if not changed.any():
            continue
        # PDF viewers resample color and soft-mask streams independently. Black
        # RGB beneath transparent pixels creates dark outlines at mask edges.
        # Supply real neighboring colors and a small unchanged pixel border so
        # interpolation cannot blend the old watermark back into the correction.
        coverage = cv2.dilate(changed.astype('uint8'), np.ones((5, 5), 'uint8')) > 0
        rgba = np.empty((*changed.shape, 4), 'uint8')
        rgba[:, :, :3] = after
        rgba[:, :, 3] = coverage.astype('uint8') * 255
        page = writer.pages[index]
        overlay = Image.fromarray(rgba)
        if page.rotation:
            overlay = overlay.rotate(page.rotation, expand=True)
        box = page.cropbox
        stream = BytesIO()
        canvas = Canvas(stream, pagesize=(float(box.width), float(box.height)))
        canvas.drawImage(ImageReader(overlay), 0, 0, width=float(box.width),
                         height=float(box.height), mask='auto')
        canvas.showPage()
        canvas.save()
        stream.seek(0)
        page.merge_transformed_page(PdfReader(stream).pages[0],
                                    Transformation().translate(float(box.left), float(box.bottom)))
    with open(output_path, 'wb') as target:
        writer.write(target)
    check = PdfReader(output_path)
    if len(check.pages) != len(reader.pages):
        raise RuntimeError('Output page count did not match.')
    for a, b in zip(reader.pages, check.pages):
        if list(a.mediabox) != list(b.mediabox) or list(a.cropbox) != list(b.cropbox) or a.rotation != b.rotation:
            raise RuntimeError('Output page geometry did not match.')
