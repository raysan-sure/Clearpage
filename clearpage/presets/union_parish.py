"""Native cleanup of the three lavender UNOFFICIAL stamps on Union Parish scans."""
from functools import lru_cache
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import json

import cv2
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from clearpage.presets.dotted_scans import replace_image
from clearpage.presets.flat_gray_scans import scan_object as simple_scan
from clearpage.presets.gray_scans import scan_object as opaque_scan
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets.screened_scans import image_stream

METHODS = ('union_parish',)
CACHE = 'union-parish-v2'


def scan_object(page):
    return simple_scan(page) or opaque_scan(page)


@lru_cache(maxsize=1)
def word_shape():
    with Image.open(RESOURCE_ROOT/'static/type-union-parish.webp') as example:
        mark = np.asarray(example.convert('RGB'))[:, :, 0] < 225
    rows, cols = np.nonzero(mark)
    return mark[rows.min():rows.max()+1, cols.min():cols.max()+1].astype('uint8')


def stamp_boxes(rgb, jpeg=False):
    height, width = rgb.shape[:2]
    red, green, blue = rgb.transpose(2, 0, 1)
    if jpeg:
        # JPEG chroma subsampling changes exact palette values. Use the softened
        # lavender only to locate the word, then verify its full silhouette.
        lavender = (blue.astype('int16') - red > 24) & (np.abs(red.astype('int16') - green) < 14)
    else:
        lavender = (red == green) & (red >= 165) & (red < 235) & (blue == 255)
    boxes = []
    for center in (.063, .503, .942):
        top = max(0, round((center-.05)*height))
        bottom = min(height, round((center+.05)*height))
        left, right = round(.15*width), round(.85*width)
        zone = lavender[top:bottom, left:right]
        rows, cols = np.nonzero(zone)
        if not len(rows):
            return []
        y, x = rows.min(), cols.min()
        h, w = rows.max()-y+1, cols.max()-x+1
        if not (.50*width < w < .66*width and .105*w < h < .15*w):
            return []
        observed = zone[y:y+h, x:x+w]
        expected = cv2.resize(word_shape(), (w,h), interpolation=cv2.INTER_NEAREST).astype(bool)
        if np.count_nonzero(expected & observed) < .82*np.count_nonzero(expected | observed):
            return []
        pad = max(3, round(w*.005))
        region = np.s_[top+y-pad:top+y+h+pad,left+x-pad:left+x+w+pad]
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*pad+1,2*pad+1))
        support = cv2.dilate(np.pad(expected.astype('uint8'), pad), kernel).astype(bool)
        boxes.append((region, support))
    return boxes


def clear_repeated_codes(rgb, result):
    # The same small gray identifier is printed in the corners and center.
    # Compare its actual glyphs on this page so that separate gray text survives.
    height, width = rgb.shape[:2]
    patches = []
    for cx, cy in ((.095,.038), (.902,.038), (.5,.5), (.095,.962), (.902,.962)):
        top, bottom = round((cy-.015)*height), round((cy+.015)*height)
        left, right = round((cx-.058)*width), round((cx+.058)*width)
        red, green, blue = rgb[top:bottom,left:right].transpose(2,0,1)
        glyph = (red == green) & (((blue == 238) & (red >= 150)) | ((blue == 11) & (red == 11)))
        rows, cols = np.nonzero(glyph)
        if not len(rows):
            continue
        y, x = rows.min(), cols.min()
        h, w = rows.max()-y+1, cols.max()-x+1
        if not (.075*width < w < .106*width and .007*height < h < .016*height):
            continue
        patches.append((np.s_[top+y:top+y+h,left+x:left+x+w], glyph[y:y+h,x:x+w]))

    for region, glyph in patches:
        h, w = glyph.shape
        votes = np.zeros((h,w), np.uint8)
        for _, other in patches:
            if other is glyph:
                continue
            # Align by translation, since QR or seal ink can hide end pixels.
            pad = max(3, round(w*.08))
            padded = cv2.copyMakeBorder(other.astype('uint8'),pad,pad,pad,pad,cv2.BORDER_CONSTANT)
            if padded.shape[0] < h or padded.shape[1] < w:
                continue
            scores = cv2.matchTemplate(padded,glyph.astype('uint8'),cv2.TM_CCORR_NORMED)
            _, score, _, (x,y) = cv2.minMaxLoc(scores)
            if score >= .60:
                votes += cv2.dilate(padded,np.ones((3,3),'uint8'))[y:y+h,x:x+w]
        confirmed = glyph & (votes >= 2)
        blue = rgb[region][:,:,2]
        result[region][confirmed] = np.where(blue[confirmed,None] == 238, 255, 0)


def jpeg_codes(rgb):
    """Locate the repeated gray identifier using clear impressions on this sheet."""
    height, width = rgb.shape[:2]
    patches = []
    half_w, half_h = round(.062*width), round(.015*width)
    for cx, cy in ((.095,.038), (.902,.038), (.5,.5), (.095,.962), (.902,.962)):
        x, y = round(cx*width), round(cy*height)
        blue = rgb[y-half_h:y+half_h,x-half_w:x+half_w,2]
        dark = cv2.dilate((blue < 128).astype('uint8'), np.ones((5,5),'uint8'))
        glyph = ((blue >= 229) & (blue <= 246) & (dark == 0)).astype('uint8')
        patches.append((x-half_w,y-half_h,glyph,int(dark.sum())))
    size = max(3, round(width*.003))
    # Estimate the code's extent from sustained rows and columns, ignoring isolated JPEG specks.
    for _, _, reference, _ in sorted(patches,key=lambda patch:patch[3]):
        ys = np.flatnonzero(reference.sum(1) > .008*width)
        xs = np.flatnonzero(reference.sum(0) > .0015*width)
        if not len(xs) or not len(ys):
            continue
        pattern = reference[ys[0]:ys[-1]+1,xs[0]:xs[-1]+1]
        h, w = pattern.shape
        if .068*width < w < .117*width and .008*width < h < .020*width:
            break
    else:
        return []
    positions = {}
    for index,(x,y,glyph,_) in enumerate(patches):
        scores = cv2.matchTemplate(glyph,pattern,cv2.TM_CCORR_NORMED)
        _, score, _, (dx,dy) = cv2.minMaxLoc(scores)
        if score > .27:
            positions[index] = (x+dx,y+dy)
    if len(positions) < 3:
        return []
    # On recording covers a seal or a QR code can obscure most of one copy.
    # The other corners establish its row and column independently.
    for index,(column,row) in {0:(3,1),1:(4,0),3:(0,4),4:(1,3)}.items():
        if index not in positions and column in positions and row in positions:
            positions[index] = (positions[column][0],positions[row][1])
    if 2 not in positions and 0 in positions and 4 in positions:
        positions[2] = tuple((a+b)//2 for a,b in zip(positions[0],positions[4]))
    return [np.s_[y-size:y+h+size,x-size:x+w+size] for x,y in positions.values()]


def clean_pixels(rgb, boxes, jpeg=False):
    result = rgb.copy()
    if jpeg:
        # These JPEGs encode binary ink. Require an empty midtone range
        # before restoring the black and white levels in any affected region.
        for box, support in [*boxes, *((region, True) for region in jpeg_codes(rgb))]:
            blue = rgb[box][:,:,2]
            if np.count_nonzero((blue > 90) & (blue < 168)) > max(4,blue.size*.0001):
                return rgb
            np.copyto(result[box],np.where(blue[:,:,None]<128,0,255).astype('uint8'),
                      where=np.asarray(support)[...,None])
        return result
    for box, support in boxes:
        red, green, blue = rgb[box].transpose(2,0,1)
        paper = (blue == 255) & (red >= 165) & (red < blue)
        ink = (red == 0) & (blue > 0) & (blue <= 70)
        result[box][support & paper & (red == green)] = 255
        result[box][support & ink & (red == green)] = 0
    clear_repeated_codes(rgb, result)
    return result


def prepare(source, index, directory, cancelled=lambda: False):
    if cancelled():
        raise InterruptedError('Cancelled')
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        return target, json.loads(metadata.read_text())
    page = read_pdf(source).pages[index]
    writer = PdfWriter()
    writer.add_page(page)
    found = scan_object(page)
    details = dict(scan_pattern=True, matched=False, changed=0, protected=0, confidence=None,
                   reviewed=False, uncertain=False, pattern_note='No matching Union Parish stamps found.')
    if found:
        name, obj = found
        jpeg = obj.get('/Filter') == '/DCTDecode'
        rgb = np.asarray(page.images[0].image.convert('RGB'))
        boxes = stamp_boxes(rgb, jpeg)
        if boxes:
            result = clean_pixels(rgb, boxes, jpeg)
        if boxes and result is not rgb:
            if np.all((result == 0).all(axis=2) | (result == 255).all(axis=2)):
                stream = image_stream(result[:,:,0] == 0)
            else:
                stream = DecodedStreamObject()
                obj = writer.pages[0]['/Resources']['/XObject'][name].get_object()
                stream.update({k:v for k,v in obj.items() if k not in ('/Filter','/DecodeParms','/Length')})
                stream.set_data(result.tobytes())
                stream = stream.flate_encode()
            replace_image(writer, writer.pages[0], name, stream)
            details.update(matched=True, changed=int(np.any(result != rgb,axis=2).sum()),
                           pattern_note='Removed the three purple UNOFFICIAL stamps and matching gray codes.')
    temporary = target.with_suffix('.part')
    try:
        writer.write(temporary)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
        metadata.write_text(json.dumps(details))
    finally:
        temporary.unlink(missing_ok=True)
    return target, details


def write_clean(source, target, keep, directory, cancelled=lambda: False):
    writer = PdfWriter(clone_from=read_pdf(source))
    for index in keep:
        cached, details = prepare(source,index,directory,cancelled)
        if details['matched']:
            obj = read_pdf(cached).pages[0]['/Resources']['/XObject']
            name, = obj.keys()
            replace_image(writer, writer.pages[index], name, obj[name].clone(writer))
    temporary = Path(str(target)+'.part')
    try:
        writer.write(temporary)
        if cancelled():
            raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
