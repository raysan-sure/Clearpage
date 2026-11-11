"""Refine the pale diagonal Cumberland scan mark using its registered color profile."""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT

import cv2
import numpy as np


@lru_cache(maxsize=1)
def _profile():
    with np.load(RESOURCE_ROOT / 'assets/cumberland-red.npz', allow_pickle=False) as data:
        return data['chroma'], data['green']


def _aligned(image):
    chroma, green = _profile()
    if min(image.size) < 300 or abs(image.width / image.height - chroma.shape[1] / chroma.shape[0]) > .025:
        return None
    small_size = (384, round(384 * image.height / image.width))
    sample = np.asarray(image.convert('RGB').resize(small_size), dtype=np.float32)
    observed = np.maximum(sample[..., 0] - sample[..., 1], 0)
    expected = cv2.resize(chroma.astype(np.float32), small_size, interpolation=cv2.INTER_AREA)
    # Require the same glyph shapes and placement throughout the page.
    for a, b in zip(np.array_split(observed, 4), np.array_split(expected, 4)):
        if np.std(a) < 1 or not np.corrcoef(a.ravel(), b.ravel())[0, 1] >= .93:
            return None
    return chroma, green


def matches(image):
    return _aligned(image) is not None


def clean(image, cancelled=lambda: False):
    from clearpage.presets.red_scans import clean_general

    if cancelled():
        raise InterruptedError('Cancelled')
    profile = _aligned(image)
    if profile is None:
        original = np.asarray(image.convert('RGB'))
        return original, np.zeros(original.shape[:2], bool), dict(
            matched=False, changed=0, uncertain=False,
            pattern_note='This page does not match the Cumberland red watermark. Page kept unchanged.')
    before, _, details = clean_general(image, cancelled)
    if not details['matched']:
        return before, np.zeros(before.shape[:2], bool), details

    chroma, green = (cv2.resize(channel, image.size) for channel in profile)
    footprint = cv2.dilate((chroma > 3).astype('uint8'), np.ones((9, 9), 'uint8')).astype(bool)
    source = np.asarray(image.convert('RGB'))
    restored = np.clip(np.rint(255 * source[..., 1].astype('float32') / np.maximum(green, 1)), 0, 255).astype('uint8')
    ink = (restored < 238) | (source.max(2) < 190)
    near_ink = cv2.dilate(ink.astype('uint8'), np.ones((3, 3), 'uint8')).astype(bool)
    background = footprint & (restored >= 246) & (~near_ink | (restored >= 253)) & (source.max(2) > 225)
    result = before.copy()
    result[background] = 255
    overlap = footprint & ~background
    result[overlap] = np.minimum(before[overlap], restored[overlap, None])
    dark_strokes = source.max(2) < 80
    result[dark_strokes] = source[dark_strokes]
    if cancelled():
        raise InterruptedError('Cancelled')
    changed = np.any(result != source, axis=2)
    details.update(changed=int(changed.sum()), cumberland_refined=True, pattern_note=(
        'Cumberland red watermark refined. The second pass preserves the darkness of detected ink. '
        'Compare overlapping text and signatures before exporting.'))
    return result, changed, details
