"""Read-only, bounded preset suggestions. No AI calls, edits, or exports."""
from PIL import Image
import numpy as np
from pdf_overlays import read_pdf
import repeated_copy
import flat_gray_scans
import tammany_scans
import red_scans
import gray_scans
import nassau_scans
import scan_patterns
import dotted_scans
import screened_scans
import kane_outline
import licensed_diagonal
import hocking_outline
import search_package_dots
import page_polarity

REASONS = {
    'hocking_outline': 'Matches the outlined Hocking County Recorder “unofficial copy” text.',
    'licensed_diagonal': 'Matches the diagonal “LICENSED TO FIRST AMERICAN DATA TREE LLC” stamp. Review crossings with words and signatures.',
    'kane_outline': 'Matches the Kane County diagonal dotted-outline “Unofficial” stamp. Review small intersections with text.',
    'screened_unofficial': 'Diagonal dotted “Un-Official” matches this scan. Removes the pattern and repairs damaged strokes; review the result.',
    'repeated_copy': 'Small gray copy words repeat across the page. Review overlaps and dark headers.',
    'flat_gray_copy': 'Matches a supported gray “Unofficial” stamp on a scan.',
    'cumberland_red': 'Matches the pale diagonal Cumberland watermark. Use this preset to refine faint traces.',
    'red_copy': 'Large pale-red diagonal lettering, like “Not an Official Copy”.',
    'overlay': 'Supported separate PDF text or watermark objects were found.',
    'unofficial_copy': 'Gray “Unofficial Copy” lettering matches the supported pattern. Cleanup adjusts to each page.',
    'nassau_unofficial': 'Pale diagonal lettering and a repeated square border match the Nassau scan type. Review faint ink after cleanup.',
    'polarity': 'Dark paper with light text looks like a negative scan.',
    'huntington_background': 'The scanned watermark matches the Huntington County pattern.',
    'repeated_document': 'Repeated diagonal lettering was detected. Overlapping text needs review.',
    'dotted_document': 'Dots match the diagonal document-watermark pattern.',
    'dotted_map': 'Isolated dots match the supported plat or survey watermark pattern.',
}


def suggest(source, count, overlays, image_for, assets):
    sampled = sorted({0, count//2, count-1})
    matches = {}
    skipped = []
    def add(method, index):
        matches.setdefault(method, []).append(index+1)
    reader = read_pdf(source)
    for index in sampled:
        try:
            page = reader.pages[index]
            if overlays[index].get('overlays'):
                add('overlay', index)
            if hocking_outline.suggested(source, index):
                add('hocking_outline', index)
                continue
            licensed = licensed_diagonal.scan_objects(page)
            if licensed and licensed_diagonal.matches(licensed_diagonal.native_pixels(page, licensed)):
                add('licensed_diagonal', index)
                continue
            screened = screened_scans.scan_objects(page)
            if screened and kane_outline.matches(screened_scans.native_pixels(page, screened)):
                add('kane_outline', index)
                continue
            if screened and screened_scans.matches(screened_scans.native_pixels(page, screened)):
                add('screened_unofficial', index)
                continue
            package_scan = search_package_dots.scan_object(page)
            if package_scan and search_package_dots.variant(search_package_dots.native_gray(page, package_scan)) >= 0:
                add('dotted_document', index)
                continue
            tammany = tammany_scans.scan_object(page)
            if tammany and tammany_scans.detected_stamp(tammany_scans.pixels(tammany)) is not None:
                add('flat_gray_copy', index)
                continue
            repeated = repeated_copy.scan_object(page)
            if repeated and repeated_copy.clean(repeated_copy.pixels(repeated))[2]['matched']:
                add('repeated_copy', index)
                continue
            found = flat_gray_scans.scan_object(page)
            gray_found = gray_scans.scan_object(page)
            native = page.images[0].image.convert('RGB') if found or gray_found else None
            if native is not None:
                if gray_found and nassau_scans.clean_pixels(np.array(native))[1]['matched']:
                    add('nassau_unofficial', index)
                    continue
                if found and flat_gray_scans.clean(native)[2]['matched']:
                    add('flat_gray_copy', index)
                    continue
                red_details = red_scans.clean(native)[2]
                if red_details['matched']:
                    add('cumberland_red' if red_details.get('cumberland_refined') else 'red_copy', index)
                    continue
                if gray_found and scan_patterns.unofficial_copy(native, assets)[2]['matched']:
                    add('unofficial_copy', index)
                    continue
            with Image.open(image_for(index)) as opened:
                rendered = opened.convert('RGB')
            if page_polarity.analyze(rendered)['detected']:
                add('polarity', index)
                continue
            # Color cleanup also supports rendered pages with other PDF layouts.
            if native is None:
                red_details = red_scans.clean(rendered)[2]
                if red_details['matched']:
                    add('cumberland_red' if red_details.get('cumberland_refined') else 'red_copy', index)
                    continue
            dots = dotted_scans.scan_object(page)
            if dots and int(dots[1]['/Width'])*int(dots[1]['/Height']) <= 12_000_000:
                pixels = dotted_scans.native_pixels(page, dots[1], rendered)
                for method in dotted_scans.METHODS:
                    if dotted_scans.clean_pixels(pixels, method)[1]:add(method,index)
                if any(index+1 in matches.get(m,[]) for m in dotted_scans.METHODS):continue
            # The same guarded detectors used by previews provide evidence.
            # Never recommend a cloud/AI draft or infer a type from the filename.
            from engine import known_mask
            _, score = known_mask(rendered, assets/'huntington.npy', min_score=.55)
            if score >= .55:
                add('huntington_background', index)
            elif scan_patterns.repeated_document(rendered)[2]['matched']:
                add('repeated_document', index)
        except (ValueError, OSError, KeyError, IndexError):
            # A detection failure must not prevent manual preview or upload.
            skipped.append(index+1)
    priority = list(REASONS)
    items = [dict(method=m, reason=REASONS[m], pages=pages)
             for m,pages in matches.items()]
    items.sort(key=lambda item: (-len(item['pages']), priority.index(item['method'])))
    return dict(items=items[:3], sampled_pages=[i+1 for i in sampled],
                skipped_pages=skipped, total_pages=count,
                note='Suggestions are based on sampled pages. Other pages may differ; preview before cleaning.')
