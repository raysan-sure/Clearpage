"""Reference-based pixel measurements, not OCR or semantic accuracy."""
import numpy as np


def validate_reference(source_path, reference_path):
    from clearpage.pdf.engine import inspect_pdf
    from clearpage.pdf.pdf_overlays import read_pdf
    if inspect_pdf(source_path) != inspect_pdf(reference_path):
        raise ValueError('Use a trusted copy with the same pages, crop dimensions and order.')
    source, reference = read_pdf(source_path), read_pdf(reference_path)
    for a, b in zip(source.pages, reference.pages):
        if list(a.cropbox) != list(b.cropbox) or a.rotation != b.rotation:
            raise ValueError('Reference crop positions and page rotations must match the source.')


def compare(source, result, reference):
    a, b, r = [np.asarray(im.convert('RGB')) for im in (source, result, reference)]
    if a.shape != b.shape or a.shape != r.shape:
        raise ValueError('Reference and result page dimensions must match exactly.')
    before_error = np.abs(a.astype(np.int16) - r.astype(np.int16))
    after_error = np.abs(b.astype(np.int16) - r.astype(np.int16))
    needs_change = np.max(before_error, axis=2) > 8
    # Preservation measures pixels that already equal the reference exactly.
    # Tiny antialiased watermark differences (1..8) are not pristine pixels.
    unchanged = np.max(before_error, axis=2) == 0
    changed = np.any(a != b, axis=2)
    reference_ink = np.mean(r, axis=2) < 128
    result_ink = np.mean(b, axis=2) < 128
    faint_ink = (np.mean(r, axis=2)>=128) & (np.mean(r, axis=2)<225)
    matches = np.max(after_error,axis=2)<=8
    overlap = int(np.sum(reference_ink & result_ink))
    def percent(n, d):
        return round(100*n/d, 2) if d else None
    def error(values, area):
        return round(float(values[area].mean()), 3) if area.any() else None
    return dict(
        pixels=int(needs_change.size),
        pixels_needing_correction=int(needs_change.sum()),
        corrected_watermark_pixels_percent=percent(np.sum(needs_change & matches),np.sum(needs_change)),
        faint_ink_pixel_match_percent=percent(np.sum(faint_ink & matches),np.sum(faint_ink)),
        unexpected_changed_pixels=int(np.sum(unchanged & changed)),
        correction_error_before=error(before_error, needs_change),
        correction_error_after=error(after_error, needs_change),
        preserved_reference_matching_pixels_percent=percent(np.sum(unchanged & ~changed), np.sum(unchanged)),
        dark_pixel_recall_percent=percent(overlap, np.sum(reference_ink)),
        dark_pixel_precision_percent=percent(overlap, np.sum(result_ink)),
        mean_rgb_error=round(float(after_error.mean()), 3),
        note='Pixel comparison only, not word accuracy. Use the same scan, crop, rotation and alignment. A shifted or incorrect reference invalidates interpretation; the app cannot verify the wording or alignment for you.')
