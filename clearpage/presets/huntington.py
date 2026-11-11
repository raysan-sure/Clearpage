"""Bounded Huntington cleanup passes, always measured against the source scan.

OCR supplies geometry only. It never supplies replacement words in this module.
Repeated passes retain the same guards so erasure cannot eat into text over time.
"""
import cv2
import numpy as np
from clearpage.restore import auto_restore


def line_gaps(source, scope, ocr):
    """Find verified gaps just above/below printed rows crossing the phrase.

    Require four intact words to establish each row. Handwriting, isolated
    labels, and words with no intact neighbours do not authorize extra removal.
    """
    h, w = scope.shape
    exclusion = cv2.dilate(scope.astype('uint8'), np.ones((5, 5), 'uint8')) > 0
    gray = source.min(2)
    candidates = np.zeros_like(scope)
    text_bands = np.zeros_like(scope)
    count = 0
    for line in (ocr or {}).get('lines', []):
        words = auto_restore.words({'lines': [line]}, scope.shape)
        reliable = [r for r in words if len(r['text']) >= 2 and 7 <= r['h'] <= 40
                    and not exclusion[r['y']:r['y']+r['h'], r['x']:r['x']+r['w']].any()]
        if len(reliable) < 4:
            continue
        top = min(r['y'] for r in reliable)
        bottom = max(r['y']+r['h'] for r in reliable)
        left = min(r['x'] for r in reliable)
        right = max(r['x']+r['w'] for r in reliable)
        if bottom-top > 40 or right-left < 180:
            continue
        # Reliable words must describe one horizontal row, not multiple columns.
        if max(r['y']+r['h']/2 for r in reliable)-min(r['y']+r['h']/2 for r in reliable) > 9:
            continue
        text_bands[max(0, top-2):min(h, bottom+2), max(0, left-12):min(w, right+12)] = True
        candidates[max(0, top-8):min(h, bottom+8), left:right] = True
        count += 1

    # Preserve external ink locally rather than keeping an entire diagonal
    # watermark component when just one of its pixels touches a printed row.
    # Tall external components still protect signatures and graphic strokes.
    ink = (gray < 245).astype('uint8')
    _, labels, boxes, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    external = ((gray < 225) & ~exclusion).astype('uint8')
    _, anchors, stats, _ = cv2.connectedComponentsWithStats(external, connectivity=8)
    tall = (stats[:, 3] > 28) & (stats[:, 4] > 40)
    tall[0] = False
    keep = boxes[:, 4] <= 12
    keep[np.unique(labels[tall[anchors]])] = True
    keep[0] = False
    guard = cv2.dilate(keep[labels].astype('uint8'), np.ones((3, 3), 'uint8')) > 0
    guard |= cv2.dilate(external, np.ones((3, 3), 'uint8')) > 0
    return candidates & ~text_bands & ~guard & scope, count


def refine(source, current, scope, protection, ocr=None, locked=None,
           cancelled=lambda: False):
    """Run each removal stage to a fixed point and report unresolved ink.

    The current image may include accepted repairs. Only source-identical pixels
    are eligible, and explicitly locked repairs are never touched.
    """
    if current.shape != source.shape or source.shape[:2] != scope.shape:
        raise ValueError('Huntington source, preview and mask dimensions differ.')
    if locked is None:
        locked = np.zeros_like(scope)
    if locked.shape != scope.shape:
        raise ValueError('Invalid Huntington repair protection mask.')
    eligible = scope & ~locked & np.all(source == current, axis=2)
    gaps, lines = line_gaps(source, scope, ocr)
    stages = [('background', scope & ~protection), ('text_line_gaps', gaps)]
    result = current.copy()
    history = []
    # Static source guards make this converge after one changing round; the
    # following round verifies that there is no further authorized erasure.
    for iteration in range(1, 4):
        if cancelled():
            raise InterruptedError('Cancelled')
        changed_round = 0
        for name, selected in stages:
            remove = selected & eligible & np.any(result != 255, axis=2)
            n = int(remove.sum())
            result[remove] = 255
            history.append(dict(iteration=iteration, stage=name, changed_pixels=n))
            changed_round += n
        if changed_round == 0:
            break
    residual = scope & ~locked & (result.min(2) < 225)
    # Residual pixels include real letters. They are candidates for review,
    # never a measurement of watermark-only pixels or an accuracy percentage.
    return result, dict(steps=history, line_guides=lines,
                        residual_review_pixels=int(residual.sum()),
                        stop_reason='text_review_required' if residual.any() else 'no_detected_residue',
                        converged=changed_round == 0)


def exact_donors(source, current, scope, source_ocr, current_ocr, index,
                 locked=None, cancelled=lambda: False):
    """Propose source-matched word patches with two agreeing OCR readings.

    Never substitutes a nearby spelling; numbers and proper names are excluded.
    Only the watermark corridor changes, so intact text remains the actual scan.
    Proposals still require visual review, especially wholly obscured letters.
    """
    gray = source.min(2)
    excluded = cv2.dilate(scope.astype('uint8'), np.ones((5, 5), 'uint8')) > 0
    occupied = np.zeros_like(scope) if locked is None else locked.copy()
    forbidden = np.zeros_like(scope)
    for ocr in (source_ocr, current_ocr):
        for word in auto_restore.words(ocr, scope.shape):
            if any(c.isdigit() for c in word['text']) or (word['text'][:1].isupper()
                    and word['text'].strip('.,;:') not in auto_restore.COMMON_CAPITALS):
                x,y,xx,yy = auto_restore.word_box(word, 1)
                forbidden[max(y,0):min(yy,gray.shape[0]),max(x,0):min(xx,gray.shape[1])] = True
    originals = auto_restore.spans(source_ocr, scope.shape)
    accepted = []
    output = current.copy()
    # Long exact phrases take precedence over contained single-word candidates.
    targets = sorted(auto_restore.spans(current_ocr, scope.shape), key=lambda r: -r['w'])
    for target in targets:
        if cancelled():
            raise InterruptedError('Cancelled')
        if not auto_restore.eligible(target) or target['text'] not in index:
            continue
        # At least three letters: tiny OCR fragments cannot establish spelling.
        if len(auto_restore.normalized(target['text'])) < 3:
            continue
        x,y,xx,yy = auto_restore.word_box(target)
        if not scope[y:yy,x:xx].any() or occupied[y:yy,x:xx].any() or forbidden[y:yy,x:xx].any():
            continue
        if not any(r['text'] == target['text'] and abs(r['x']-x)<=4 and abs(r['y']-y)<=4
                   and abs(r['w']-target['w'])<=6 and abs(r['h']-target['h'])<=5 for r in originals):
            continue
        donors = sorted(index[target['text']], key=lambda r:abs(r['word']['w']/target['w']-1)
                        +abs(r['word']['h']/target['h']-1))[:16]
        best = None
        for donor in donors:
            fit = auto_restore.align(gray, excluded, target, donor)
            if fit and fit['score'] >= .95 and (best is None or fit['score'] > best['score']):
                best = dict(fit, donor=donor)
        if best is None:
            continue
        bx,by,bxx,byy = best['box']
        if bx < x-4 or by < y-4 or bxx > xx+4 or byy > yy+4:
            continue
        if forbidden[by:byy,bx:bxx].any() or occupied[by:byy,bx:bxx].any():
            continue
        visible = ~excluded[by:byy,bx:bxx]
        ink = 255-best['gray'].astype('float32')
        if ink[visible].sum() < ink.sum()*.65:
            continue
        # Enough surviving ink in every vertical slice prevents an entirely
        # hidden letter from being accepted on the strength of the other letters.
        for col in range(0, ink.shape[1], max(4, round(target['h']*.35))):
            part = ink[:,col:col+max(4,round(target['h']*.35))]
            seen = visible[:,col:col+part.shape[1]]
            if part.sum()>400 and (part*seen).sum()<part.sum()*.25:
                break
        else:
            region = output[by:byy,bx:bxx]
            selected = scope[by:byy,bx:bxx]
            patch = np.repeat(best['gray'][:,:,None], 3, axis=2)
            delta = np.abs(region.astype('int16')-patch.astype('int16')).max(2)
            if np.count_nonzero(selected & (delta>60)) < 5:
                continue
            # Keep the complete word patch for the existing manual review path,
            # but preserve every original pixel outside the matched watermark.
            proposed = region.copy()
            proposed[selected] = patch[selected]
            output[by:byy,bx:bxx] = proposed
            occupied[by:byy,bx:bxx] = True
            accepted.append(dict(text=target['text'], box=best['box'], score=best['score'],
                donor_page=best['donor']['page'], donor_box=auto_restore.word_box(best['donor']['word'],2),
                patch=proposed, method='huntington-exact-donor'))
    return output, accepted
