"""Native cleanup for pale diagonal lettering inside a repeating tiled border.

The templates contain only the repeating tile and the isolated watermark design.
Only matched, simple portrait RGB scans qualify. No OCR or generated text.
"""
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import json
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets.dotted_scans import replace_image
from clearpage.presets.gray_scans import scan_object

METHODS = ('nassau_unofficial',)
CACHE = 'native-nassau-v2'


@lru_cache(maxsize=1)
def layout_profile():
    with Image.open(RESOURCE_ROOT/'assets/nassau-layout.png') as image:
        depth = np.array(image)
    # Compare the dark cores of the design with neighboring paper in four
    # independent regions. Dense text may obscure an edge or half of the word.
    core = depth >= 22
    nearby = (cv2.dilate(core.astype('uint8'), np.ones((9, 9), 'uint8')) > 0) & (depth == 0)
    y, x = np.indices(depth.shape)
    center = (x >= 96) & (x <= 704) & (y > 94) & (y < 944)
    regions = (x < 96, x > 704, center & (y < 500), center & (y >= 500))
    probes = [(np.flatnonzero(core & r), np.flatnonzero(nearby & r)) for r in regions]
    return depth, probes


def clean_known_layout(a, gray):
    depth, probes = layout_profile()
    h, w = gray.shape
    small = cv2.resize(gray, (depth.shape[1], depth.shape[0]), interpolation=cv2.INTER_AREA).ravel()
    matches = []
    for strokes, gaps in probes:
        ink, paper = small[strokes], small[gaps]
        ink, paper = ink[ink >= 160], paper[paper >= 160]
        matches.append(len(ink) >= .2*len(strokes) and len(paper) >= .2*len(gaps)
                       and np.mean(ink < 245) >= .75
                       and np.mean(ink < 245) - np.mean(paper < 245) >= .4)
    if sum(matches) < 3 or not any(matches[:2]) or not any(matches[2:]):
        return None

    # Allow for JPEG fringes and subpixel shifts between scan resolutions.
    # Bound the correction by the measured depth of the nearby template.
    envelope = cv2.dilate(depth, np.ones((5, 5), 'uint8'))
    lift = cv2.resize(envelope, (w, h), interpolation=cv2.INTER_LINEAR)
    eligible = (lift > 0) & (gray >= 175) & ((a.max(2) - gray) < 20)
    # Fade the correction near dark strokes, where little paper is visible.
    lift = np.where(eligible, (lift.astype('float32') + 4) * np.clip(
        (gray.astype('float32') - 175) / 45, 0, 1), 0).round().astype('uint8')
    result = cv2.add(a, cv2.merge((lift, lift, lift)))
    return result, int(np.count_nonzero((lift > 0) & (gray < 255)))


@lru_cache(maxsize=1)
def tile_pattern():
    tile = np.load(RESOURCE_ROOT/'assets/nassau-border.npy', allow_pickle=False)
    if tile.dtype != np.uint8 or tile.shape != (26, 26):
        raise ValueError('Invalid tiled-border pattern. Reinstall the complete application.')
    tile.setflags(write=False)
    return tile


def border_match(gray):
    h, w = gray.shape
    small = cv2.resize(gray, (768, round(h*768/w)), interpolation=cv2.INTER_AREA)
    stretch = small.shape[0]/1000
    pitch = 32*stretch
    counts = []
    rows = []
    for lo, hi in ((45, 105), (705, 760)):
        region = small[:, lo:hi]
        scores = np.zeros(region.shape[0], 'float32')
        for size in (24, 26, 28):
            tile = cv2.resize(tile_pattern(), (size, max(12, round(size*stretch))))
            correlation = cv2.matchTemplate(region, tile, cv2.TM_CCOEFF_NORMED)
            scores[:len(correlation)] = np.maximum(scores[:len(correlation)], correlation.max(1))
        peaks = np.flatnonzero((scores > .65) &
            (scores == cv2.dilate(scores[:, None], np.ones((max(3, round(21*stretch)), 1), 'uint8'))[:, 0]))
        if len(peaks) < 5:
            return False, counts + [int(len(peaks))]
        gaps = np.diff(peaks)
        regular = np.abs(gaps - np.maximum(1, np.rint(gaps/pitch))*pitch) <= 4*stretch
        if regular.mean() < .60:
            return False, counts + [int(len(peaks))]
        counts.append(int(len(peaks)))
        rows.append(peaks)
    # Text can obscure most of one edge. Require a long surviving edge and
    # matching tile rows on the other, rather than erasing through the text.
    shorter, longer = sorted(rows, key=len)
    aligned = np.min(np.abs(shorter[:, None]-longer[None, :]), axis=1) <= 5*stretch
    return (sum(counts) >= 20 and max(np.ptp(row) for row in rows) >= small.shape[0]*.55
            and aligned.mean() >= .70), counts


def clean_pixels(a, cancelled=lambda: False):
    details = dict(native_nassau=True, matched=False, changed=0, confidence=None,
                   protected=0, reviewed=False, uncertain=False,
                   note='No matching pale diagonal watermark with a tiled border. Original retained.')
    if cancelled():
        raise InterruptedError('Cancelled')
    if a.ndim != 3 or a.shape[2] != 3:
        return a, details
    h, w = a.shape[:2]
    if not 400 <= w <= 3000 or not 1.15 <= h/w <= 1.9 or h*w > 12_000_000:
        return a, details
    gray = a.min(2)
    known = clean_known_layout(a, gray)
    if known is not None:
        if cancelled():
            raise InterruptedError('Cancelled')
        result, count = known
        details.update(matched=True, changed=count, uncertain=True,
            note='Matched the Nassau lettering and tile layout. Dark and colored ink retained. Review crossings with faint ink.')
        return result, details
    if w < 750:
        return a, details
    matched, counts = border_match(gray)
    details['border_tiles'] = counts
    if not matched:
        return a, details
    if cancelled():
        raise InterruptedError('Cancelled')
    yy, xx = np.indices((h, w), dtype=np.int32)
    protected = cv2.dilate((gray < 175).astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    neutral = a.max(2).astype('int16') - gray.astype('int16') < 20
    votes = ((gray >= 175) & (gray < 247) & neutral & ~protected &
             (xx > .14*w) & (xx < .9*w) & (yy > .10*h) & (yy < .92*h))
    histogram = np.bincount((xx+yy)[votes], minlength=w+h)
    half = round(w*.16)
    scores = np.convolve(histogram, np.ones(2*half+1), 'same')
    center = int(np.argmax(scores))
    agreement = float(scores[center]/max(1, votes.sum()))
    if votes.sum() < w*w*.001 or agreement < .40:
        return a, details
    band = np.abs(xx+yy-center) < w*.25
    border = (((xx > .06*w) & (xx < .13*w)) |
              ((xx > .925*w) & (xx < .985*w)) |
              ((yy > .035*h) & (yy < .105*h)) |
              ((yy > .935*h) & (yy < .99*h)))
    remove = (band | border) & neutral & ~protected & (gray >= 175) & (gray < 255)
    count = int(remove.sum())
    if count < w*w*.003:
        return a, details
    if cancelled():
        raise InterruptedError('Cancelled')
    result = a.copy()
    result[remove] = 255
    details.update(matched=True, changed=count, protected=int(protected.sum()),
        uncertain=True, diagonal_agreement=round(agreement, 3),
        note='Pale diagonal lettering and the tiled border lightened at original resolution. Dark ink and its immediate edges are retained. Review faint gray text, signatures and intersections before exporting.')
    return result, details


def prepare(source, index, directory, assets=None, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    found = scan_object(page)
    details = dict(native_nassau=True, matched=False, changed=0, confidence=None,
        protected=0, reviewed=False, uncertain=False,
        note='Unsupported scan structure. Original retained.')
    writer = PdfWriter()
    writer.add_page(page)
    if found:
        name, obj = found
        a = np.array(page.images[0].image.convert('RGB'))
        if a.shape != (int(obj['/Height']), int(obj['/Width']), 3):
            raise ValueError('The scan dimensions do not match the PDF.')
        result, details = clean_pixels(a, cancelled)
        if details['changed']:
            original = writer.pages[0]['/Resources']['/XObject'][name].get_object()
            image = DecodedStreamObject()
            image.update({k: v for k, v in original.items() if k not in ('/Filter', '/DecodeParms', '/Length')})
            image.set_data(result.tobytes())
            replace_image(writer, writer.pages[0], name, image.flate_encode())
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
    writer = PdfWriter()
    writer.clone_document_from_reader(read_pdf(source))
    for index in keep:
        if cancelled():
            raise InterruptedError('Cancelled')
        path = Path(directory)/f'{index}-{CACHE}.pdf'
        details = json.loads(path.with_suffix('.json').read_text())
        if not details['changed']:
            continue
        found = scan_object(read_pdf(path).pages[0])
        if found is None:
            raise ValueError('Invalid tiled-border scan cache. Process again.')
        name, image = found
        replace_image(writer, writer.pages[index], name, image.clone(writer))
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
