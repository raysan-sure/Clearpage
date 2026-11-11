"""Conservative negative-scan detection and reversible RGB inversion."""
import cv2
import numpy as np


def analyze(image):
    rgb = np.asarray(image.convert('RGB'))
    # Sample at bounded resolution; the correction itself never resizes pixels.
    scale = min(1., 1200 / max(rgb.shape[:2]))
    sample = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    gray = cv2.cvtColor(sample, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    inner = gray[h//10:max(h//10+1, h-h//10), w//10:max(w//10+1, w-w//10)]
    dark = float(np.mean(inner < 75))
    bright = float(np.mean(inner > 180))
    neutral = float(np.mean(np.ptp(sample.astype(np.int16), axis=2) < 25))
    # Require dark paper across the page, not simply a black border or illustration.
    tiles = [np.mean(t < 75) for row in np.array_split(inner, 4) for t in np.array_split(row, 4) if t.size]
    coverage = float(np.mean(np.array(tiles) > .55))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((inner > 180).astype('uint8'), 8)
    max_height = max(5, inner.shape[0] * .06)
    glyphs = sum(3 <= area <= inner.size*.003 and 2 <= height <= max_height and width <= inner.shape[1]*.12
                 for x, y, width, height, area in stats[1:])
    detected = dark > .65 and .002 < bright < .28 and neutral > .94 and coverage >= .875 and glyphs >= 8
    if detected:
        note = 'Likely negative scan: dark paper with small light marks. Review the inverted preview before selecting this page.'
    elif dark > .55:
        note = 'Dark page, but not a clear negative text scan. Original retained; use the per-page override only after checking it.'
    else:
        note = 'No clear negative scan detected. Original page retained.'
    return dict(detected=bool(detected), dark_background_percent=round(dark*100, 1),
                light_marks=int(glyphs), note=note)


def correct(image, force=False):
    details = analyze(image)
    source = np.array(image.convert('RGB'))
    applied = details['detected'] or force
    result = 255 - source if applied else source.copy()
    if force:
        details['note'] = 'Manual inversion requested for this page. Check text, photographs, stamps and signatures before selecting it.'
    return result, dict(details, applied=bool(applied), forced=bool(force))
