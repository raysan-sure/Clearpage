"""Explicit scan presets with conservative matching and no generated lettering."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from vendor.purescan_core import invert_multiply

METHODS = ('huntington_background', 'unofficial_copy', 'red_copy', 'cumberland_red', 'flat_gray_copy', 'repeated_copy', 'repeated_diagonal', 'repeated_diagonal_ai', 'repeated_document')
MAX_PIXELS = 12_000_000
GRAY_LAYOUTS = (1019, 1032, 1994, 2019, 2024, 2029, 2536, 2538)


def unchanged(a, note):
    return a.copy(), np.zeros(a.shape[:2], bool), dict(
        scan_pattern=True, confidence=None, matched=False, changed=0,
        protected=0, reviewed=False, uncertain=False, pattern_note=note)


def huntington_background(image, assets, cancelled=lambda: False, ocr=None):
    """Explicit Huntington background preset; never reconstruct covered words.

    Match the supplied phrase rather than selecting an arbitrary diagonal band.
    Protect horizontal document ink locally, rather than preserving the entire
    watermark component when it touches one letter. Tall non-text strokes and
    isolated punctuation remain protected. No lettering is generated.
    """
    from clearpage.pdf.engine import known_mask
    a = np.array(image.convert('RGB'))
    if image.width * image.height > MAX_PIXELS:
        raise ValueError('This scan is too large for Huntington background cleanup.')
    if cancelled():
        raise InterruptedError('Cancelled')
    mask, score = known_mask(image, Path(assets)/'huntington.npy', min_score=.55)
    if not mask.any():
        return unchanged(a, 'No confident Huntington background pattern match. Original page retained.')
    protected = huntington_ink_protection(a, mask)
    from clearpage.presets import huntington
    result, passes = huntington.refine(a, a, mask, protected, ocr=ocr, cancelled=cancelled)
    if cancelled():
        raise InterruptedError('Cancelled')
    changed = np.any(result != a, axis=2)
    return result, changed, dict(
        scan_pattern=True, confidence=round(score, 3), matched=True,
        changed=int(changed.sum()), protected=int((mask & protected).sum()),
        reviewed=False, uncertain=bool(changed.any()), partial=True, **passes,
        pattern_note='Huntington passes finished: background and verified text-line gaps cleaned until no further pixels qualified. Remaining intersections need text review; use Word repair tools to preview and confirm an intact scanned word or exact wording. Repeating removal cannot recover opaque, hidden letters.')


def huntington_ink_protection(a, mask):
    """Local ink guards; the diagonal component itself cannot guard its gaps."""
    gray = a.min(axis=2)
    exclusion = cv2.dilate(mask.astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    # Faint source ink outside the matched glyphs anchors text rows. Closing
    # bridges the narrow diagonal cut; margins retain descenders and word ends.
    anchors = ((gray < 225) & ~exclusion).astype('uint8')
    bounds = cv2.morphologyEx(anchors, cv2.MORPH_CLOSE, np.ones((1, 61), 'uint8'))
    bounds = cv2.dilate(bounds, np.ones((5, 21), 'uint8')) > 0
    support = cv2.boxFilter(anchors.astype('float32'), -1, (121, 3), normalize=True)
    rows = bounds & (support > .04)
    protected = cv2.dilate(rows.astype('uint8'), np.ones((5, 5), 'uint8')) > 0

    ink = (gray < 225).astype('uint8')
    _, labels, boxes, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    small = (boxes[:, 4] <= 12) & (boxes[:, 2] <= 5) & (boxes[:, 3] <= 6)
    small[0] = False
    protected |= cv2.dilate(small[labels].astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    # Retain signatures, borders, map strokes and other tall source components.
    # Measure the fragments outside the mask so watermark letters alone cannot
    # make every intersecting printed word into one protected diagonal string.
    _, anchor_labels, anchor_boxes, _ = cv2.connectedComponentsWithStats(anchors, connectivity=8)
    tall = (anchor_boxes[:, 3] > 28) & (anchor_boxes[:, 4] > 40)
    tall[0] = False
    touching = np.unique(labels[tall[anchor_labels]])
    touching = touching[touching != 0]
    if touching.size:
        protected |= cv2.dilate(np.isin(labels, touching).astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((19, 1), 'uint8'))
    protected |= cv2.dilate(vertical, np.ones((3, 3), 'uint8')) > 0
    return protected


def gray_watermark_residue(result, selected, cancelled=lambda: False):
    """Clear inverse-blend speckle only within confidently matched gray glyphs.

    Compression noise survives division as pale, disconnected edge fragments.
    Keep substantial ink, long faint strokes, and components that continue out
    of the matched area, including their antialiased edges. Work from a fixed
    image so repeated erosion cannot eat into document content.
    """
    gray = result.min(2)
    scale = result.shape[1] / 1541
    _, labels, boxes, _ = cv2.connectedComponentsWithStats(
        (gray < 210).astype('uint8'), connectivity=8)
    substantial = boxes[:, 4] >= max(4, round(4 * scale * scale))
    substantial[0] = False
    protected = substantial[labels] | (gray < 170)
    if cancelled():
        raise InterruptedError('Cancelled')
    _, labels, boxes, _ = cv2.connectedComponentsWithStats(
        (gray < 245).astype('uint8'), connectivity=8)
    length = max(12, round(20 * scale))
    connected = (boxes[:, 2] >= length) | (boxes[:, 3] >= length)
    connected[np.unique(labels[(gray < 245) & ~selected])] = True
    connected[0] = False
    protected |= connected[labels]
    radius = max(1, round(scale))
    protected = cv2.dilate(protected.astype('uint8'),
                           np.ones((2*radius+1, 2*radius+1), 'uint8')) > 0
    clear = selected & ~protected & (gray >= 170) & (gray < 255)
    cleaned = result.copy()
    cleaned[clear] = 255
    return cleaned, int(clear.sum())


def gray_watermark_edges(result, selected, seed, cancelled=lambda: False):
    """Remove pale contour fragments that trace the known watermark boundary.

    Long residual edges otherwise look like protected map lines. Require almost
    the entire connected fragment to follow the matched glyph edge, with no dark
    ink or continuation outside the selection. Crossing document strokes fail
    this test and remain intact, as do their antialiased neighbours.
    """
    gray = result.min(2)
    radius = max(1, round(2 * result.shape[1] / 1541))
    edge = cv2.morphologyEx((seed.min(2) < 225).astype('uint8'),
                            cv2.MORPH_GRADIENT,
                            np.ones((2*radius+1, 2*radius+1), 'uint8')) > 0
    count, labels, boxes, _ = cv2.connectedComponentsWithStats(
        (gray < 250).astype('uint8'), connectivity=8)
    hits = np.bincount(labels[edge], minlength=count)
    contours = hits / np.maximum(boxes[:, 4], 1) > .95
    contours[np.unique(labels[(gray < 170) | ~selected])] = False
    contours[0] = False
    if cancelled():
        raise InterruptedError('Cancelled')
    fragments = contours[labels]
    fringe = max(1, round(result.shape[1] / 1541))
    kernel = np.ones((2*fringe+1, 2*fringe+1), 'uint8')
    remove = cv2.dilate(fragments.astype('uint8'), kernel) > 0
    protected = cv2.dilate(((gray < 245) & ~fragments).astype('uint8'), kernel) > 0
    remove &= selected & ~protected & (gray < 255)
    cleaned = result.copy()
    cleaned[remove] = 255
    return cleaned, int(remove.sum())


def unofficial_copy(image, assets, cancelled=lambda: False):
    if cancelled(): raise InterruptedError('Cancelled')
    fixed = _unofficial_copy_fixed(image, assets, cancelled)
    # Keep exact well-matched legacy layouts; realign weak or unknown layouts.
    if fixed[2].get('pattern_match',0) >= .90:
        return fixed
    from clearpage.presets.aligned_gray import clean
    aligned = clean(image, assets, cancelled)
    return aligned if aligned is not None else fixed


def _unofficial_copy_fixed(image, assets, cancelled=lambda: False):
    a = np.array(image.convert('RGB'))
    h, w = a.shape[:2]
    if h*w > MAX_PIXELS:
        raise ValueError('This scan is too large for gray-watermark cleanup.')
    candidates = []
    for height in GRAY_LAYOUTS:
        error = abs(w/h - 1541/height)
        if error < .008:
            candidates.append((error, height))
    if not candidates:
        return unchanged(a, 'This page does not match a supported Unofficial Copy layout. Original retained.')
    height = min(candidates)[1]
    path = Path(assets)/f'unofficial-copy-{height}.npz'
    if not path.is_file():
        raise ValueError('The Unofficial Copy pattern asset is missing. Reinstall the complete application.')
    with np.load(path, allow_pickle=False) as data:
        template, seed, scope = data['template'], data['seed'], data['scope']
    if (h, w) != seed.shape[:2]:
        template = cv2.resize(template, (w, h), interpolation=cv2.INTER_LINEAR)
        seed = cv2.resize(seed, (w, h), interpolation=cv2.INTER_LINEAR)
        scope = cv2.resize(scope.astype('uint8'), (w, h), interpolation=cv2.INTER_NEAREST) > 0
    gray = a.min(2)
    ink = seed.min(2) < 225
    # Require the known glyphs and their white surroundings to agree. A clean
    # page, a different diagonal mark, or a displaced pattern must not qualify.
    visible = ink & (gray > 64)
    coverage = float(np.mean(gray[ink] < 235)) if ink.any() else 0.
    agreement = float(np.mean(np.abs(a.astype('float32')[visible] - template[visible]) < 45)) if visible.any() else 0.
    edge = cv2.dilate(ink.astype('uint8'), np.ones((13,13),'uint8')) > 0
    edge &= ~cv2.dilate(ink.astype('uint8'), np.ones((3,3),'uint8')).astype(bool)
    edge_white = float(np.mean(gray[edge] > 225)) if edge.any() else 0.
    if coverage < .75 or agreement < .70 or edge_white < .65:
        return unchanged(a, 'No confident Unofficial Copy pattern match. Original page retained.')
    if cancelled():
        raise InterruptedError('Cancelled')
    selected = scope & (seed.min(2) >= 64) & (gray > 32)
    result = invert_multiply(a, template, selected)
    near_ink = cv2.dilate((result.min(2) < 180).astype('uint8'), np.ones((3,3),'uint8')) > 0
    result[selected & ~near_ink & (result.min(2) >= 235)] = 255
    result, residue = gray_watermark_residue(result, selected, cancelled)
    result, outlines = gray_watermark_edges(result, selected, seed, cancelled)
    if cancelled():
        raise InterruptedError('Cancelled')
    changed = np.any(result != a, axis=2)
    return result, changed, dict(scan_pattern=True, confidence=None, pattern_match=round(agreement, 3),
        matched=bool(changed.any()), changed=int(changed.sum()), protected=int((scope & (gray <= 32)).sum()),
        reviewed=False, uncertain=bool(changed.any()), residue_removed=residue,
        outlines_removed=outlines,
        pattern_note='Unofficial Copy reverse blending, compression-speckle and matched-outline cleanup applied. Dark ink and strokes crossing the watermark boundary are protected; check words and map symbols beneath the mark before selecting this page.')


def _repeated_detection(image, cancelled=lambda: False):
    """Shared regular-line detector; no reconstruction is performed here."""
    a = np.array(image.convert('RGB'))
    gray = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    if h*w > MAX_PIXELS:
        raise ValueError('This scan is too large for repeated-watermark cleanup.')
    scale = w / 1200
    def px(value):
        return max(1, round(value * scale))
    if cancelled(): raise InterruptedError('Cancelled')
    def no_match(note):
        return a, gray, None, None, None, note
    _, labels, stats, _ = cv2.connectedComponentsWithStats((gray < 220).astype('uint8'), connectivity=8)
    small = ((stats[:,4] >= max(1, 2*scale*scale)) & (stats[:,4] <= 80*scale*scale) &
             (stats[:,2] <= px(23)) & (stats[:,3] <= px(23)))
    small[0] = False
    candidates = small[labels].astype('uint8')
    lines = cv2.HoughLines(candidates, 1, np.pi/180, max(60,int(w*.12)),
                           min_theta=np.pi*25/180, max_theta=np.pi*65/180)
    if lines is None:
        return no_match('No repeated diagonal lettering detected. Original retained.')
    angles = np.round(lines[:,0,1]*180/np.pi).astype(int)
    angle = int(np.bincount(angles).argmax())
    y, x = np.ogrid[:h,:w]
    rho = x*np.cos(angle*np.pi/180) + y*np.sin(angle*np.pi/180)
    projection = np.bincount(np.round(rho).astype(int).ravel(), weights=candidates.ravel())
    smooth = np.convolve(projection, np.ones(px(15)), mode='same')
    peaks = []
    strength = {}
    for _ in range(30):
        p = int(smooth.argmax())
        if smooth[p] < w*.25: break
        peaks.append(p)
        strength[p] = float(smooth[p])
        smooth[max(0,p-px(50)):p+px(50)+1] = 0
    weak_peaks = peaks
    peaks = [p for p in peaks if strength[p] >= w]
    if len(peaks) < 4:
        return no_match('Not enough repeated diagonal lines to distinguish a watermark. Original retained.')
    # Keep a regular sequence, rather than unrelated diagonal annotations.
    best = []
    best_period = None
    for left in peaks:
        for right in peaks:
            period = abs(right-left)
            if not px(85) <= period <= min(w,h)*.4: continue
            fit = [p for p in peaks if abs((p-left+period/2) % period-period/2) < px(8)]
            if len(fit) > len(best):
                best, best_period = fit, period
    if len(best) < 4:
        return no_match('The diagonal marks do not form a supported repeated pattern. Original retained.')
    # Clipped edge copies have fewer letters, so they cannot establish a
    # pattern themselves. Include them only after the strong repeats establish
    # the same spacing, and only at an observed peak (never an invented band).
    origin = best[0]
    best += [p for p in weak_peaks if p not in best and
             abs((p-origin+best_period/2) % best_period-best_period/2) < px(8)]
    band = np.zeros(gray.shape, bool)
    for p in best: band |= np.abs(rho-p) < px(18)
    if cancelled(): raise InterruptedError('Cancelled')
    return a, gray, labels, stats, band, ''


def repeated_ai_mask(image, cancelled=lambda: False):
    """Return the full reconstruction scope (also used by export checks)."""
    selected, _, note = repeated_ai_plan(image, cancelled)
    return selected, note


def repeated_ai_plan(image, cancelled=lambda: False):
    """Keep full-band cleanup and separately identify observed source strokes.

    Some intersecting original strokes remain inseparable. The caller
    must label predictions as generated drafts, never as exact restorations.
    """
    a, gray, labels, stats, band, note = _repeated_detection(image, cancelled)
    if band is None:
        empty = np.zeros(a.shape[:2], bool)
        return empty, empty.copy(), note
    selected = band & (gray < 245)
    selected = (cv2.dilate(selected.astype('uint8'), np.ones((3,3), 'uint8')) > 0) & band
    count = int(selected.sum())
    if count < 300 or count > gray.size*.25:
        empty = np.zeros(gray.shape, bool)
        return empty, empty.copy(), 'The detected area is too small or too broad for automatic AI cleanup. Original retained; use a custom painted area instead.'
    donors, content, _, _ = _repeated_regions(gray, labels, stats, band, cancelled)
    from clearpage.presets import repeated_template
    retained, learned = repeated_template.retained_source_strokes(gray, selected, donors, content, band, cancelled)
    prefix = ('Full-band cleanup with supported original strokes and faint edges copied back afterward. '
              if learned and retained.any() else 'Full-band cleanup; no reliable source-stroke restoration was found. ')
    return selected, retained, prefix + 'AI reconstruction draft: letters, numbers, signatures and barcode data at overlaps remain unverified. Review every affected area before selecting this page. Complete removal and text accuracy are not verified.'


def repeated_diagonal(image, cancelled=lambda: False):
    """Remove lettering in blank areas; preserve the content rectangle."""
    a, gray, labels, stats, band, note = _repeated_detection(image, cancelled)
    if band is None:
        return unchanged(a, note)
    h, w = gray.shape
    # Horizontal runs of dark body text, rules, signatures and barcodes establish
    # one conservative content rectangle. Never erase inside that rectangle.
    horizontal = cv2.morphologyEx((gray < 100).astype('uint8'), cv2.MORPH_CLOSE, np.ones((3,31),'uint8'))
    _, _, boxes, _ = cv2.connectedComponentsWithStats(horizontal, connectivity=8)
    body = boxes[1:][(boxes[1:,2] >= 55) & (boxes[1:,4] >= 120) & (boxes[1:,2] > boxes[1:,3]*1.5)]
    if len(body) < 3:
        return unchanged(a, 'Could not establish a protected document-content area. Original retained.')
    margin = max(20, round(w*.025))
    x0=max(0,int(body[:,0].min())-margin); y0=max(0,int(body[:,1].min())-margin)
    x1=min(w,int((body[:,0]+body[:,2]).max())+margin); y1=min(h,int((body[:,1]+body[:,3]).max())+margin)
    protected = np.zeros(gray.shape, bool); protected[y0:y1,x0:x1] = True
    isolated = (stats[:,4] >= 1) & (stats[:,4] <= 400) & (stats[:,2] <= 40) & (stats[:,3] <= 40)
    isolated[np.unique(labels[protected | ~band])] = False
    remove = isolated[labels]
    # Clear only pale compression fringes immediately next to selected dots.
    edge = cv2.dilate(remove.astype('uint8'),np.ones((3,3),'uint8')) > 0
    remove |= edge & (gray >= 220) & ~protected & band
    if np.count_nonzero(remove & (gray < 220)) < 300:
        return unchanged(a, 'No removable diagonal lettering outside the protected content area. Original retained.')
    if cancelled(): raise InterruptedError('Cancelled')
    result = a.copy(); result[remove] = 255
    changed = np.any(result != a, axis=2)
    return result, changed, dict(scan_pattern=True, confidence=None, matched=True,
        changed=int(changed.sum()), protected=int((protected & band & (gray<220)).sum()),
        reviewed=False, uncertain=True, partial=True, protected_box=[x0,y0,x1,y1],
        pattern_note='Partial cleanup: diagonal lettering removed in blank areas. The entire document-content area is preserved exactly, including overlapping watermark. This is not a fully clean copy.')


def _repeated_regions(gray, labels, stats, band, cancelled=lambda: False):
    """Find isolated watermark donors without using document rows as samples."""
    h, w = gray.shape
    scale = w / 1200
    def px(n):
        return max(1, round(n * scale))

    # Exclude the diagonal bands when finding horizontal rows. Otherwise the
    # watermark joins adjacent lines into one huge component and no row passes
    # the height check (common on dense deeds and their recording headers).
    ink = (gray < 220).astype('uint8')
    anchors = ink & (~band).astype('uint8')
    horizontal = cv2.morphologyEx(anchors, cv2.MORPH_CLOSE,
                                 np.ones((1, px(85)), 'uint8'))
    _, groups, boxes, _ = cv2.connectedComponentsWithStats(horizontal, connectivity=8)
    rows = ((boxes[:, 2] >= px(35)) & (boxes[:, 3] >= px(5)) &
            (boxes[:, 3] <= px(36)) & (boxes[:, 2] > boxes[:, 3] * 2))
    rows[0] = False
    if rows.sum() < 3:
        return np.zeros(gray.shape, bool), np.ones(gray.shape, bool), 0, 'Could not establish document text rows. Original retained.'
    protected = np.zeros(gray.shape, bool)
    for x, y, width, height, _ in boxes[rows]:
        # A row may start/end inside the excluded band. Extend by a whole band
        # crossing so obscured leading letters, account numbers and punctuation
        # are retained as well, not only the intact words used as anchors.
        margin = max(60, px(60))
        protected[max(0,y-px(3)):min(h,y+height+px(3)),
                  max(0,x-margin):min(w,x+width+margin)] = True
    # Large connected strokes include signatures, seals, rules and barcodes.
    structural = ((stats[:, 2] > px(40)) | (stats[:, 3] > px(32)) |
                  (stats[:, 4] > px(400) * scale))
    structural[0] = False
    protected |= cv2.dilate(structural[labels].astype('uint8'),
                            np.ones((px(5), px(5)), 'uint8')) > 0
    # Enclosed stamps and handwriting can contain disconnected dots or letters
    # entirely within a band. Protect their interiors, not just their connected
    # outline, so those details cannot be mistaken for isolated watermark ink.
    for x, y, width, height, _ in stats[structural]:
        if height > px(32) and width > px(40):
            protected[max(0,y-px(2)):min(h,y+height+px(2)),
                      max(0,x-px(2)):min(w,x+width+px(2))] = True
    # Retain complete connected components whenever any part is protected or
    # outside the repeated bands. This also protects text/watermark crossings.
    eligible = ((stats[:, 2] <= px(32)) & (stats[:, 3] <= px(32)) &
                (stats[:, 4] <= 400 * scale * scale))
    eligible[np.unique(labels[protected | ~band])] = False
    eligible[0] = False
    remove = eligible[labels]
    if np.count_nonzero(remove) < 300 * scale * scale:
        return np.zeros(gray.shape, bool), protected, int(rows.sum()), 'No isolated repeated lettering could be separated from document ink. Original retained.'
    # JPEG halos are pale and confined to one pixel around selected glyphs.
    edge = cv2.dilate(remove.astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    remove |= edge & (gray >= 220) & ~protected & band
    if cancelled():
        raise InterruptedError('Cancelled')
    return remove, protected, int(rows.sum()), ''


def repeated_document(image, cancelled=lambda: False):
    """Remove isolated glyphs; retain original ink at unresolved intersections."""
    a, gray, labels, stats, band, note = _repeated_detection(image, cancelled)
    if band is None:
        return unchanged(a, note)
    remove, protected, rows, note = _repeated_regions(gray, labels, stats, band, cancelled)
    if note:
        return unchanged(a, note)
    result = a.copy()
    result[remove] = 255
    from clearpage.presets import repeated_template
    result, learned = repeated_template.refine(gray, result, remove, band, cancelled)
    changed = np.any(result != a, axis=2)
    return result, changed, dict(
        scan_pattern=True, confidence=None, matched=True, partial=True,
        changed=int(changed.sum()), protected=int((protected & band & (gray < 220)).sum()),
        reviewed=False, uncertain=True, line_guides=rows, **learned,
        residual_review_pixels=int((band & (result.min(2) < 220)).sum()),
        pattern_note='Non-AI cleanup: isolated lettering and verified repeating watermark pixels removed. Intact blank-area copies guide an additional pass; dark strokes, faint text rows and signatures remain protected. Opaque overlaps may remain. No words are generated and exact recovery is not guaranteed; compare names, numbers and signatures before export.')
