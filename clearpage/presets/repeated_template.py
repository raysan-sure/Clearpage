"""Learn repeated raster lettering from the current scan, without AI or OCR.

Only blank-area watermark samples from the conservative pass are donors. A
repeat is accepted from several independently matched patches. This does not
recover content destroyed by opaque ink; unresolved intersections stay intact.
"""
import warnings
import cv2
import numpy as np


def repeat_vector(gray, donors, cancelled):
    # Discovery is bounded; cleanup always runs at original resolution.
    factor = min(1., 1200 / gray.shape[1])
    if factor < 1:
        size = (round(gray.shape[1]*factor), round(gray.shape[0]*factor))
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        donors = cv2.resize(donors.astype('uint8'), size, interpolation=cv2.INTER_NEAREST) > 0
    h, w = gray.shape
    side = max(64, round(w * .088))
    patches = []
    for y in range(0, h-side, side//2):
        for x in range(0, w-side, side//2):
            ink = gray[y:y+side, x:x+side] < 220
            count = int(ink.sum())
            if count > side*1.7 and (donors[y:y+side,x:x+side] & ink).sum() >= count*.98:
                patches.append((count,x,y))
    chosen = []
    for _, x, y in sorted(patches, reverse=True):
        if all(abs(x-xx) >= side or abs(y-yy) >= side for xx,yy in chosen):
            chosen.append((x,y))
        if len(chosen) == 16:
            break
    shifts = []
    for index,(x,y) in enumerate(chosen):
        if cancelled(): raise InterruptedError('Cancelled')
        scores = cv2.matchTemplate(gray, gray[y:y+side,x:x+side], cv2.TM_CCOEFF_NORMED)
        for _ in range(12):
            _, score, _, (xx,yy) = cv2.minMaxLoc(scores)
            if score < .86: break
            dx,dy = xx-x,yy-y
            if dx < 0 or (dx == 0 and dy < 0): dx,dy = -dx,-dy
            if dx*dx + dy*dy > side*side:
                shifts.append((dx,dy,index))
            scores[max(0,yy-20):yy+21,max(0,xx-20):xx+21] = -1
    candidates = []
    for dx,dy,_ in shifts:
        aligned = [(x,y,i) for x,y,i in shifts if abs(x-dx)<=2 and abs(y-dy)<=2]
        count = len(set(i for _,_,i in aligned))
        if count >= 3:
            candidates.append((count, -(dx*dx+dy*dy), dx,dy))
    if not candidates:
        return None
    _,_,dx,dy = max(candidates)
    # Refine fractional repeat spacing using independently observed multiples.
    fits = []
    for x,y,_ in shifts:
        k = round((x*dx+y*dy)/(dx*dx+dy*dy))
        if k >= 1 and abs(x-k*dx)<=3 and abs(y-k*dy)<=3:
            fits.append((k,x,y))
    divisor = sum(k*k for k,_,_ in fits)
    return (sum(k*x for k,x,_ in fits)/divisor/factor,
            sum(k*y for k,_,y in fits)/divisor/factor)


def retained_source_strokes(gray, broad, donors, content, band, cancelled=lambda: False):
    """Locate original strokes and faint edges for post-inpainting copy-back.

    Learn both ink and whitespace from blank-area repeats. Two distinct copies
    must cover a source pixel, which must be darker than every nearby reference
    and inside an established content region. Faint edges additionally need
    three copies with wholly light reference neighborhoods. Unknown pixels do not reduce the
    inpainting mask. This preserves observations, never generates wording.
    """
    if cancelled():
        raise InterruptedError('Cancelled')
    if not donors.any():
        return np.zeros(gray.shape, bool), False
    vector = repeat_vector(gray, donors, cancelled)
    if vector is None:
        return np.zeros(gray.shape, bool), False
    h, w = gray.shape
    support = np.zeros(gray.shape, 'uint8')
    low = np.full(gray.shape, 255, 'uint8')
    kernel = np.ones((3, 3), 'uint8')
    valid = ((donors | (gray >= 235)) & ~content & band).astype('uint8')
    # A donor neighborhood must be wholly valid so document edges cannot leak
    # into the learned background. Darkest-neighbor comparison tolerates the
    # fractional repeat spacing without restoring nearby watermark pixels.
    valid = cv2.erode(valid, kernel)
    donor_dark = cv2.erode(gray, kernel)
    shifts = set((round(vector[0]*k), round(vector[1]*k))
                 for k in range(-6, 7) if k)
    for dx, dy in sorted(shifts):
        if cancelled():
            raise InterruptedError('Cancelled')
        if (dx == 0 and dy == 0) or abs(dx) >= w or abs(dy) >= h:
            continue
        transform = np.float32([[1, 0, dx], [0, 1, dy]])
        stamp = cv2.warpAffine(valid, transform, (w, h), flags=cv2.INTER_NEAREST) > 0
        values = cv2.warpAffine(donor_dark, transform, (w, h),
                               flags=cv2.INTER_NEAREST, borderValue=255)
        support += stamp
        low = np.minimum(low, np.where(stamp, values, 255))
    darker = (gray.astype('int16') < low.astype('int16') - 35) & (gray < 100)
    # The old dark-stroke rule omits antialiasing and faint document ink. Copy
    # those observed pixels only where three independent repeat positions all
    # show white, including their 3x3 neighborhoods. Keep the darkest reference
    # (not a median): even one nearby watermark stroke vetoes this expansion.
    faint = (support >= 3) & (low >= 245) & (gray >= 100) & (gray < 230)
    retained = broad & content & ~donors & (((support >= 2) & darker) | faint)
    if cancelled():
        raise InterruptedError('Cancelled')
    return retained, True


def refine(gray, baseline, donors, band, cancelled=lambda: False):
    vector = repeat_vector(gray, donors, cancelled)
    if vector is None:
        return baseline, dict(template_matched=False, template_extra_pixels=0)
    h,w = gray.shape
    shifts = [(round(vector[0]*k),round(vector[1]*k)) for k in range(-6,7) if k]
    shifts = [(x,y) for x,y in shifts if abs(x)<w and abs(y)<h]
    # Preserve dark original strokes and their antialiasing. The opaque parts
    # of a watermark are intentionally not erased by guessing what lies below.
    scale = w/1200
    guard_size = max(3, round(3*scale) | 1)
    guard_kernel = np.ones((guard_size,guard_size),'uint8')
    protected = cv2.dilate((gray<45).astype('uint8'),guard_kernel) > 0
    _,_,boxes,_ = cv2.connectedComponentsWithStats((gray<110).astype('uint8'),8)
    for x,y,bw,bh,_ in boxes[1:]:
        if bh > 35*scale or (bw>40*scale and bh>32*scale):
            protected[max(0,y-2):min(h,y+bh+2),max(0,x-2):min(w,x+bw+2)] = True
    # Faint text cannot be separated reliably by darkness: preserve its rows.
    anchors = ((gray<225)&~band).astype('uint8')
    lines = cv2.morphologyEx(anchors,cv2.MORPH_CLOSE,np.ones((1,max(1,round(85*scale))),'uint8'))
    _,_,rows,_ = cv2.connectedComponentsWithStats(lines,8)
    for x,y,bw,bh,area in rows[1:]:
        if bw>35*scale and 4*scale<bh<36*scale and bw>bh*2:
            pixels = gray[y:y+bh,x:x+bw][anchors[y:y+bh,x:x+bw]>0]
            if len(pixels) and np.percentile(pixels,25)>100:
                protected[max(0,y-3):y+bh+3,max(0,x-round(65*scale)):x+bw+round(65*scale)] = True
    remove = np.zeros(gray.shape,bool)
    for block_top in range(0,h,128):
        if cancelled(): raise InterruptedError('Cancelled')
        block_bottom = min(h,block_top+128)
        # Include neighboring rows when protecting strokes at chunk boundaries.
        top = max(0,block_top-guard_size//2)
        bottom = min(h,block_bottom+guard_size//2)
        samples = np.full((len(shifts),bottom-top,w),np.nan,'float32')
        for i,(dx,dy) in enumerate(shifts):
            y0,y1 = max(top,dy),min(bottom,h+dy)
            x0,x1 = max(0,dx),min(w,w+dx)
            if y1<=y0 or x1<=x0: continue
            valid = donors[y0-dy:y1-dy,x0-dx:x1-dx]
            values = gray[y0-dy:y1-dy,x0-dx:x1-dx].astype('float32')
            values[~valid] = np.nan
            samples[i,y0-top:y1-top,x0:x1] = values
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            estimate = np.nanmedian(samples,axis=0)
            low,high = np.nanmin(samples,axis=0),np.nanmax(samples,axis=0)
        count = np.isfinite(samples).sum(axis=0)
        observed = gray[top:bottom].astype('float32')
        match = ((count>=2)&(high-low<=40)&(observed>=estimate-20)&
                 (observed<=estimate+30)&(estimate<235))
        # Keep darker-than-watermark ink, including local antialiased edges.
        stroke = observed < np.nan_to_num(low,nan=255)-35
        guard = cv2.dilate(stroke.astype('uint8'),guard_kernel)>0
        local = match & ~guard & ~protected[top:bottom] & band[top:bottom]
        remove[block_top:block_bottom] = local[block_top-top:block_bottom-top]
    result = baseline.copy()
    extra = int((remove & np.any(baseline != 255,axis=2)).sum())
    result[remove] = 255
    return result, dict(template_matched=True, template_extra_pixels=extra,
                        repeat_vector=[round(v,3) for v in vector])
