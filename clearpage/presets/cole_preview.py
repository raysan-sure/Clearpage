"""Cole County preview scans: remove the repeated print and repair corroborated words."""
import hashlib
import re
import zlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pikepdf
from PIL import Image

from clearpage.paths import RESOURCE_ROOT
from clearpage.presets.henry_red import save
from clearpage.presets.hocking_outline import replace_resource
from clearpage.presets.native_scan_profile import image_entry

METHODS = ('cole_preview',)
SIZE = (779, 1019)
JPEG_SIZES = {(681, 898), (681, 899)}
CACHE = 'cole-preview-v2'
# Each box is in native scan coordinates, exclusive at right and bottom.
LABELS = ((6,10,98,48), (7,520,92,558), (109,30,671,59),
          (109,957,669,983), (620,267,774,303), (620,776,774,811))
JPEG_LABELS = ((6, 8, 80, 42), (6, 456, 81, 492), (100, 28, 581, 50),
               (100, 844, 581, 868), (546, 234, 677, 266), (546, 683, 677, 716))
JPEG_DIAGONALS = ((90, 120, 152, 190), (516, 120, 583, 190),
                  (395, 535, 452, 600), (220, 535, 277, 600))


def crop(array, box):
    x, y, right, bottom = box
    return array[y:bottom, x:right]


def digest(data):
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=3)
def stamp(size=SIZE):
    filename = 'cole-preview.png' if size == SIZE else f'cole-preview-{size[1]}.png'
    with Image.open(RESOURCE_ROOT / 'assets' / filename) as img:
        return np.array(img.convert('L'))


def matches(gray):
    size = gray.shape[::-1]
    if size in JPEG_SIZES:
        paper = stamp(size)
        for box in JPEG_LABELS + JPEG_DIAGONALS:
            observed, expected = crop(gray, box), crop(paper, box)
            ink = expected < 150
            if np.mean(observed[ink] <= expected[ink].astype(int) + 25) < .90:
                return False
            if np.mean(observed[expected == 255] > 210) < .40:
                return False
        return True
    paper = stamp()
    if gray.shape != paper.shape:
        return False
    for box in LABELS:
        observed, expected = crop(gray, box), crop(paper, box)
        printed = expected < 160
        if np.mean(observed[printed] <= expected[printed].astype(int) + 8) < .94:
            return False
        if np.mean(observed[expected == 255] > 210) < .40:
            return False
    # Require both diagonals as well as the labels.
    for y in (175, 660):
        for x in (int(13+(y-13)*741/981), int(753-(y-13)*741/981)):
            region = (x-45,y-45,x+45,y+45)
            ref, scan = crop(paper,region), crop(gray,region)
            line = ref < 150
            if np.mean(scan[line] <= ref[line].astype(int)+12) < .90:
                return False
    return True


def scan(page):
    found = image_entry(page, 8, {'/DeviceGray'}, {'/FlateDecode'},
                        lambda w,h: (w,h) == SIZE)
    if found is None:
        found = image_entry(page, 8, {'/DeviceRGB', '/DeviceGray'}, {'/DCTDecode'},
                            lambda w, h: (w, h) in JPEG_SIZES, allow_opaque_mask=True)
        if found is None:
            return None
    name, obj = found
    pixels = np.asarray(pikepdf.PdfImage(obj).as_pil_image(apply_mask=False))
    if pixels.ndim == 3:
        if pixels.shape[2] != 3 or not (np.array_equal(pixels[:,:,0], pixels[:,:,1])
                                        and np.array_equal(pixels[:,:,0], pixels[:,:,2])):
            return None
        pixels = pixels[:,:,0]
    return (name, pixels) if matches(pixels) else None


def suggested(source, index):
    with pikepdf.open(source) as doc:
        return scan(doc.pages[index]) is not None


def display_image(source, index):
    """Keep the native scan samples when the page paints only that full-page image."""
    with pikepdf.open(source) as doc:
        page = doc.pages[index]
        objects = page.Resources.get('/XObject', {})
        if len(objects) != 1:
            return None
        name, obj = next(iter(objects.items()))
        if obj.get('/Subtype') != '/Image':
            return None
        size = int(obj.Width), int(obj.Height)
        if size != SIZE and size not in JPEG_SIZES:
            return None
        left, bottom, right, top = map(float, page.cropbox)
        width, height = right - left, top - bottom
        if min(width, height) <= 0 or min(size[0]/width, size[1]/height) <= 2:
            return None
        ops = list(pikepdf.parse_content_stream(page))
        if (not page.get('/Rotate', 0) and not page.get('/Annots')
                and [str(ins.operator) for ins in ops] == ['q', 'cm', 'Do', 'Q']
                and str(ops[2].operands[0]) == name
                and np.allclose(list(map(float, ops[1].operands)),
                                [width, 0, 0, height, left, bottom], rtol=0, atol=.001)
                and abs(size[0] * height / width - size[1]) < 1
                and '/OC' not in obj and '/Group' not in page
                and obj.get('/ColorSpace') in (pikepdf.Name.DeviceGray, pikepdf.Name.DeviceRGB)
                and not any(key in obj for key in ('/Mask', '/ImageMask', '/Decode'))):
            # scan() accepts fully opaque soft masks. Other masks need the PDF compositor.
            if '/SMask' not in obj or scan(page) is not None:
                return pikepdf.PdfImage(obj).as_pil_image(apply_mask=False).copy()
    # Preserve overlays, crop and rotation when displaying a composed PDF page.
    from clearpage.pdf.engine import render_page
    return render_page(source, index, scale=max(size[0]/width, size[1]/height))


def restore(gray):
    paper = stamp(gray.shape[::-1])
    out = gray.copy()
    visible = (paper < 255) & (paper >= 45)
    out[visible] = np.minimum(255, np.rint(gray[visible].astype('float32')*255/paper[visible]))
    opaque = paper < 60
    y, x = np.nonzero(opaque)
    height, width = gray.shape
    predictions, costs = [], []
    for dx, dy in ((1,0),(0,1),(1,1),(1,-1),(2,1),(1,2),(2,-1),(1,-2)):
        samples, spans = [], []
        for sign in (-1,1):
            distance = np.ones(x.size, dtype=int)
            for _ in range(9):
                px = np.clip(x+sign*distance*dx,0,width-1)
                py = np.clip(y+sign*distance*dy,0,height-1)
                missing = opaque[py,px]
                if not missing.any():
                    break
                distance[missing] += 1
            px = np.clip(x+sign*distance*dx,0,width-1)
            py = np.clip(y+sign*distance*dy,0,height-1)
            samples.append(out[py,px].astype('float32'))
            spans.append(distance*np.hypot(dx,dy)+100*opaque[py,px])
        left,right = samples
        dl,dr = spans
        predictions.append((left*dr+right*dl)/(dl+dr))
        costs.append(.3*np.abs(left-right)+6*(dl+dr))
    weights = np.exp(-(np.stack(costs)-np.min(costs,axis=0))/8)
    out[y,x] = np.rint((weights*np.stack(predictions)).sum(0)/weights.sum(0))
    if gray.shape[::-1] in JPEG_SIZES:
        # JPEG ringing extends beyond the letters. Clear it only on blank paper;
        # nearby ink absent from the template may be faint handwriting or print.
        printed = (paper < 210).astype(np.uint8)
        footprint = cv2.dilate(printed, np.ones((3, 3), np.uint8))
        evidence = ((gray < 230) & (footprint == 0)).astype(np.uint8)
        near_ink = cv2.dilate(evidence, np.ones((9, 9), np.uint8))
        margin = cv2.dilate(printed, np.ones((17, 17), np.uint8))
        out[(margin != 0) & (near_ink == 0)] = 255
    return out


# Only matching reviewed line fragments may use these word placements. The
# donor pixels must also be present in the current PDF. No deed text image is bundled.
LINES = (
    ((605,780,725,805), 'd55a8d19bda42170949006ec511937f7b6746c6f6427e6536146e790058b8dc1',
     '2e1437ec1c22ef9fa462f5d1875f464e988da117b083211a3c75aa2fb8de2413',
     'day',603, (('and',628),('year',648),('first',670),('above',693)), (
        ((626,783,647,801),(541,289,562,307),'e9b0b700cbdf01a65e01252ffddfd173999fe4f9584ad5af0b670894fed65cc2'),
        ((647,783,669,801),(696,289,720,307),'a74f2af7af4ea72a77838f684d42d248a197b19b628031faf0501617cf998172'),
        ((669,783,692,801),(50,306,73,324),'4701f4041508ee0455b10cffd7a0c3b56fb2050675b345596c8f4c7c8634702e'),
        ((692,783,723,801),(72,306,104,324),'7df8cfa1592149f598a2f1b558d9559396fd8e8a58d4ac534bafcc8033ed08f7'))),
    ((635,285,725,308),'8362b64950e2493ceb8d2d55f3efdfbaab46b276c7ee22b48ec42af14cc69c0c',
     '5e0c9bb9b5469617f851a65ae60b43b091acf121b30feb263415d4a2640f295f',
     'the',640,(('day',658),('and',678),('year',698)), (
        ((675,289,696,305),(541,290,562,306),'746400d20b755694f4ede25dd4fc54a3c1480b69e77fb2d2fd15f30b29164e04'),
        ((655,289,676,305),(605,784,626,800),'6c8a9875e497fc531ea2f349be354d1196a66cac37caff8da2a90c9fdeef4c59'))),
)


def fit_word(target, donor, layer):
    h,w = target.shape
    u,v = np.meshgrid(np.arange(w,dtype='float32'),np.arange(h,dtype='float32'))
    weight = np.clip((layer.astype(float)-80)/165,0,1)
    def trial(dx,dy,sx,sy):
        mx = ((u-(w-1)/2)/sx+(donor.shape[1]-1)/2+dx).astype('float32')
        my = ((v-(h-1)/2)/sy+(donor.shape[0]-1)/2+dy).astype('float32')
        fit = cv2.remap(donor,mx,my,cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=255)
        error = np.average((fit.astype('float32')-target)**2,weights=weight)
        return error,fit
    best = (float('inf'),None)
    for sx in (.9,.95,1,1.05,1.1):
        for sy in (.9,1,1.1):
            for dx in np.arange(-2,2.01,.5):
                for dy in np.arange(-1.75,1.76,.5):
                    error,_ = trial(dx,dy,sx,sy)
                    if error < best[0]:
                        best = error,(dx,dy,sx,sy)
    dx,dy,sx,sy = best[1]
    for x in np.arange(dx-.5,dx+.51,.125):
        for y in np.arange(dy-.5,dy+.51,.125):
            error,_ = trial(x,y,sx,sy)
            if error < best[0]:
                best = error,(x,y,sx,sy)
    _,fit = trial(*best[1])
    alpha = np.clip((255.-layer)/140,0,1)
    target[:] = np.rint(target*(1-alpha)+fit*alpha)


def repair_words(doc, index, gray, out, line, cancelled):
    remaining = list(line[-1])
    for i in (index, *(j for j in range(len(doc.pages)) if j != index)):
        if cancelled():
            raise InterruptedError('Cancelled')
        if i == index:
            donor_scan = gray
        else:
            found = scan(doc.pages[i])
            if found is None:
                continue
            donor_scan = found[1]
        hits = [r for r in remaining if digest(crop(donor_scan,r[1]).tobytes())==r[2]]
        if hits:
            # Read the clean donor before applying any word patches to it.
            donor_page = restore(donor_scan)
            for r in hits:
                fit_word(crop(out,r[0]),crop(donor_page,r[1]),crop(stamp(),r[0]))
                remaining.remove(r)
        if not remaining:
            break
    return len(line[-1])-len(remaining)


OCR = re.compile(rb'q 0 0 0 rg 0 0 0 RG BT ([0-9. \-]+) Tm (/FXF\d+) [0-9.]+ Tf 3 Tr \[<([0-9A-Fa-f]+)>\] TJ ET Q')
NOTICE = 'the image quality of this document may be degraded for'


def clean_ocr(page, line, repairs):
    contents = page.Contents
    raw = b''.join(s.read_bytes() for s in contents) if isinstance(contents,pikepdf.Array) else contents.read_bytes()
    width,height = map(float,page.mediabox[2:])
    notices = [[],[]]
    replacements = {}
    reviewed = line is not None and digest(raw)==line[2]
    for match in OCR.finditer(raw):
        matrix = match[1].split()
        if len(matrix)!=6 or len(match[3])%4:
            continue
        x = float(matrix[-2])*779/width
        y = 1019-float(matrix[-1])*1019/height
        word = bytes.fromhex(match[3].decode()).decode('utf-16be', errors='replace').strip().lower()
        if word and 108<x<650 and (30<y<60 or 957<y<990):
            notices[int(y>950)].append((word, match.start()))
        if (word=='cole' and x<35 and (y<65 or 500<y<570) or
            (word=='preview' or reviewed and word=='review') and x>610 and (265<y<320 or 770<y<820)):
            replacements[match.start()] = b''
        if reviewed and repairs==len(line[-1]) and word==line[3] and abs(x-line[4])<=2:
            suffix = []
            for label,left in line[5]:
                encoded = label.encode('utf-16be').hex().upper()
                suffix.append(f'q BT .76 0 0 .75 {left*width/779:.4f} {matrix[-1].decode()} Tm {match[2].decode()} 12 Tf 3 Tr [<{encoded}>] TJ ET Q'.encode())
            replacements[match.start()] = b'\n'.join([match[0],*suffix])
    for tokens in notices:
        if tokens and ' '.join(w for w,_ in tokens[:-1])==NOTICE and tokens[-1][0] in ('preview.','previee.'):
            replacements.update((pos,b'') for _,pos in tokens)
    return OCR.sub(lambda m:replacements.get(m.start(),m[0]),raw) if replacements else None


def prepare(source,index,directory,cancelled=lambda:False):
    if cancelled():
        raise InterruptedError('Cancelled')
    target = Path(directory)/f'{index}-{CACHE}.pdf'
    details = dict(scan_pattern=True,matched=False,changed=0,protected=0,reviewed=False,
                   uncertain=False,confidence=None,word_repairs=0)
    with pikepdf.open(source) as doc,pikepdf.new() as preview:
        preview.pages.append(doc.pages[index])
        page = preview.pages[0]
        found = scan(doc.pages[index])
        if found:
            name,gray = found
            out = restore(gray)
            legacy_layout = gray.shape[::-1] == SIZE
            line = next((r for r in LINES if digest(crop(gray,r[0]).tobytes())==r[1]),None) if legacy_layout else None
            count = repair_words(doc,index,gray,out,line,cancelled) if line else 0
            obj = page.Resources.XObject[name]
            # The preview owns its copied page and image.
            obj.write(zlib.compress(out.tobytes()),filter=pikepdf.Name.FlateDecode)
            obj.ColorSpace = pikepdf.Name.DeviceGray
            if '/SMask' in obj:
                del obj['/SMask']  # scan() verified that every alpha sample is opaque.
            if '/DecodeParms' in obj:
                del obj['/DecodeParms']
            stream = clean_ocr(page,line,count) if legacy_layout else None
            if stream is not None:
                page.Contents = preview.make_stream(stream)
            details.update(matched=True,uncertain=True,changed=int((gray!=out).sum()),word_repairs=count,
                           pattern_note=f'Cole preview removed. {count} words repaired from matching print. Review crossings.')
        save(preview,target,cancelled)
    return target,details


def write_clean(source,target,keep,directory,cancelled=lambda:False):
    with pikepdf.open(source) as doc:
        for i in keep:
            if cancelled():
                raise InterruptedError('Cancelled')
            page = doc.pages[i]
            if scan(page) is None:
                continue
            with pikepdf.open(Path(directory)/f'{i}-{CACHE}.pdf') as cached:
                reviewed = cached.pages[0]
                name = next(iter(reviewed.Resources.XObject.keys()))
                replace_resource(page,name,doc.copy_foreign(reviewed.Resources.XObject[name]))
                # Contents may originally be an array. Copy its decoded drawing
                # commands so OCR and the native image match the selected preview.
                data = reviewed.Contents
                raw = b''.join(s.read_bytes() for s in data) if isinstance(data,pikepdf.Array) else data.read_bytes()
                page.Contents = doc.make_stream(raw)
        save(doc,target,cancelled)
