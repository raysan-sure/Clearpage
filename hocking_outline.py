"""Native scan cleanup for Hocking County Recorder outlined unofficial-copy lettering."""
from functools import lru_cache
from pathlib import Path
import json
import hashlib
import zlib
import cv2 as cv
import numpy as np
import pikepdf

METHODS = ('hocking_outline',)
CACHE = 'hocking-outline-v1'

@lru_cache(maxsize=1)
def profile():
    data = (Path(__file__).parent/'assets/hocking-outline.png').read_bytes()
    if hashlib.sha256(data).hexdigest() != '07098ea16006c35c233ab7a7a5ea5a2ae42358cd090823a61edadf58b8bff46b':
        raise ValueError('Reinstall the Hocking County outline profile.')
    base = cv.imdecode(np.frombuffer(data, np.uint8), cv.IMREAD_GRAYSCALE)
    return base


@lru_cache(maxsize=1)
def layouts():
    calibration=json.loads((Path(__file__).parent/'assets/hocking-layouts.json').read_text())
    return {key:np.asarray(value,np.float64) for key,value in calibration.items()}


def locate(scan):
    """Align several separated sections, then verify the entire outline."""
    if scan is None or scan.ndim!=2 or scan.dtype!=np.uint8:return None
    h,w=scan.shape
    if not (1450<=w<=1900 and 1950<=h<=3300):return None
    base=profile()
    # Letter and tall scans use different print scales. Correlate several
    # independent word sections and reject incompatible positions as outliers.
    guess,offset=(.77,(45,135)) if h<2300 else ((1.057,(-11,-34)) if h>3000 else (1.,(0,0)))
    pairs=[]
    for left in range(220,1430,85):
        right=left+80
        yy=np.nonzero(base[:,left:right]<180)[0]
        if not len(yy):continue
        top,bottom=int(yy.min())-3,int(yy.max())+4
        tile=cv.resize(255-base[top:bottom,left:right],None,fx=guess,fy=guess)
        x=round(guess*left+offset[0]); y=round(guess*top+offset[1]);pad=55
        th,tw=tile.shape
        if x-pad<0 or y-pad<0 or x+tw+pad>w or y+th+pad>h:continue
        area=255-scan[y-pad:y+th+pad,x-pad:x+tw+pad]
        score=cv.matchTemplate(area,tile,cv.TM_CCOEFF_NORMED)
        _,corr,_,(dx,dy)=cv.minMaxLoc(score)
        if corr>.22:
            pairs.append(((left+(right-left)/2,top+(bottom-top)/2),
                          (x+dx-pad+tw/2,y+dy-pad+th/2)))
    if len(pairs)<7:return None
    src,dst=map(np.float32,zip(*pairs))
    M,inliers=cv.estimateAffinePartial2D(src,dst,method=cv.RANSAC,ransacReprojThreshold=3)
    if M is None or abs(M[0,0]-guess)>.055 or abs(M[0,1])>.019:return None
    selected=src[inliers.ravel()!=0,0]
    if len(selected)<7 or np.ptp(selected)<720:return None
    # Keep the template and the local refinement windows inside this scan.
    ends = np.float32([[210,1855],[1460,615]]) @ M[:,:2].T + M[:,2]
    if np.any(ends<60) or np.any(ends>np.array([w,h])-60): return None
    # Refine noisy text-heavy fits against the measured printer layouts only
    # after the independent word matches have established the same position.
    preset=layouts().get(f'{w}x{h}')
    if preset is not None:
        probes=np.float32([[215,1855],[840,1235],[1460,615]])
        residual=probes@(M[:,:2]-preset[:,:2]).T+(M[:,2]-preset[:,2])
        if np.max(np.linalg.norm(residual,axis=1))<4:M=preset
    fitted = fit(base,scan,M)
    # Some printer layouts retain the original grayscale screen exactly. Its
    # intermediate tones distinguish black document ink from the outline even
    # when most of a letter has been interrupted by foreground text.
    cy,cx=np.where((base>12)&(base<241))
    if cx.max()<w and cy.max()<h:
        same=scan[cy,cx]==base[cy,cx]
        if all(same[k].mean()>.18 for k in np.array_split(np.argsort(cx),6)):
            fitted=np.full_like(scan,255)
            fitted[cy.min()-12:cy.max()+13,cx.min()-12:cx.max()+13]=base[cy.min()-12:cy.max()+13,cx.min()-12:cx.max()+13]
    # Evidence must extend across the mark: a partial county name, an
    # unrelated diagonal label, or ordinary body text should be left alone.
    scores=[]
    for left in np.array_split(np.arange(int(ends[0,0]),int(ends[1,0])),6):
        x0,x1=left[0],left[-1]+1
        ink=fitted[:,x0:x1]<180
        if not ink.any():return None
        paper=(cv.dilate(ink.astype('uint8'),np.ones((11,11),'uint8'))>0)&~ink
        nearby=cv.dilate((255-scan[:,x0:x1]),np.ones((3,3),'uint8'))
        scores.append(float(nearby[ink].mean()-(255-scan[:,x0:x1])[paper].mean())/255)
    return fitted if min(scores)>.12 and np.median(scores)>.32 else None


def image_entry(page):
    objects=page.Resources.get('/XObject',{})
    if len(objects)!=1 or page.obj.get('/Rotate',0)%360: return None
    name,obj=next(iter(objects.items()))
    if (obj.get('/Subtype')!='/Image' or obj.get('/ColorSpace')!='/DeviceRGB'
            or obj.get('/BitsPerComponent')!=8 or obj.get('/Filter')!='/FlateDecode'
            or not 1450<=obj.get('/Width',0)<=1900 or not 1950<=obj.get('/Height',0)<=3300
            or any(k in obj for k in ('/SMask','/Mask','/Decode','/ImageMask'))):
        return None
    draws=[str(args[0]) for args,op in pikepdf.parse_content_stream(page) if str(op)=='Do' and len(args)==1]
    return (name,obj) if draws==[name] else None


def native(entry):
    if entry is None:return None
    obj=entry[1];raw=obj.read_bytes();h,w=int(obj.Height),int(obj.Width)
    if len(raw)!=h*w*3:return None
    a=np.frombuffer(raw,np.uint8).reshape(h,w,3)
    if not (np.array_equal(a[:,:,0],a[:,:,1]) and np.array_equal(a[:,:,0],a[:,:,2])):return None
    return a[:,:,0].copy()


def suggested(source,index):
    with pikepdf.open(source) as f:
        return locate(native(image_entry(f.pages[index]))) is not None


def clean_pixels(scan,cancelled=lambda:False):
    if cancelled():raise InterruptedError('Cancelled')
    details=dict(native_hocking=True,scan_pattern=True,matched=False,changed=0,
                 protected=0,confidence=None,uncertain=False,reviewed=False,
                 pattern_note='Original page kept: no matching Hocking County outline.')
    fitted=locate(scan)
    if fitted is None:return scan,details
    if cancelled():raise InterruptedError('Cancelled')
    first,_=remove_components(scan,fitted)
    result=refine(first,fitted)
    if cancelled():raise InterruptedError('Cancelled')
    changed=result!=scan
    distance=cv.distanceTransform((fitted>=248).astype('uint8'),cv.DIST_L2,5)
    if np.any(distance[changed]>=6.01) or np.any(result[changed]!=255):
        raise ValueError('Hocking outline preservation check failed.')
    details.update(matched=True,changed=int(changed.sum()),uncertain=True,
        pattern_note='Removed the matched Hocking County outline. Review where it crossed text, signatures or seals.')
    return result,details


def replace_resource(page,name,obj):
    resources=pikepdf.Dictionary(page.Resources)
    objects=pikepdf.Dictionary(resources.XObject);objects[name]=obj
    resources.XObject=objects;page.Resources=resources


def prepare(source,index,directory,cancelled=lambda:False):
    if cancelled():raise InterruptedError('Cancelled')
    target=Path(directory)/f'{index}-{CACHE}.pdf'
    meta=target.with_suffix('.json')
    if target.exists() and meta.exists():return target,json.loads(meta.read_text())
    part=target.with_suffix('.part')
    try:
        with pikepdf.open(source) as f, pikepdf.new() as single:
            page=f.pages[index];entry=image_entry(page)
            result,details=clean_pixels(native(entry),cancelled)
            single.pages.append(page)
            if details['matched']:
                name,obj=entry
                im=single.make_stream(zlib.compress(cv.cvtColor(result,cv.COLOR_GRAY2RGB).tobytes()))
                for k,v in single.pages[0].Resources.XObject[name].items():
                    if k not in ('/Length','/Filter','/DecodeParms'):im[k]=v
                im.Filter=pikepdf.Name.FlateDecode
                replace_resource(single.pages[0],name,im)
            single.save(part)
        if cancelled():raise InterruptedError('Cancelled')
        part.replace(target);meta.write_text(json.dumps(details))
    finally:part.unlink(missing_ok=True)
    return target,details


def write_clean(source,target,keep,directory,cancelled=lambda:False):
    part=Path(str(target)+'.part')
    try:
        with pikepdf.open(source) as f:
            for i in keep:
                cached,details=prepare(source,i,directory,cancelled)
                if not details['matched']:continue
                with pikepdf.open(cached) as single:
                    name,obj=image_entry(single.pages[0])
                    replace_resource(f.pages[i],name,f.copy_foreign(obj))
            if cancelled():raise InterruptedError('Cancelled')
            f.save(part)
        if cancelled():raise InterruptedError('Cancelled')
        part.replace(target)
    finally:part.unlink(missing_ok=True)

def fit(base,a,M):
    pre=cv.warpAffine(base,M,(a.shape[1],a.shape[0]),borderValue=255)
    # Fit each small section against the native scan, allowing for the font
    # being rasterized independently at the different page resolutions.
    fitted=np.full_like(a,255)
    for left in range(0,a.shape[1],45):
        right=min(left+45,a.shape[1]);yy,xx=np.where(pre[:,left:right]<225)
        if not len(yy):continue
        y0=max(12,yy.min()-4);y1=min(a.shape[0]-12,yy.max()+5)
        x0=max(12,left-8);x1=min(a.shape[1]-12,right+8)
        tile=255-pre[y0:y1,x0:x1]
        score=cv.matchTemplate(255-a[y0-9:y1+9,x0-9:x1+9],tile,cv.TM_CCOEFF_NORMED)
        _,corr,_,(dx,dy)=cv.minMaxLoc(score);dx-=9;dy-=9
        if corr<.17 or abs(dx)>5 or abs(dy)>5:dx=dy=0
        if left+dx<0 or right+dx>a.shape[1]: dx=0
        patch=pre[y0:y1,left:right]
        reg=fitted[y0+dy:y1+dy,left+dx:right+dx]
        np.minimum(reg,patch,out=reg)
    return fitted
def remove_components(a,template):
    ink=(a<214); tm=template<248
    dt=cv.distanceTransform((~tm).astype('uint8'),cv.DIST_L2,5)
    near=dt<=3.2
    n,lab,st,_=cv.connectedComponentsWithStats(ink.astype('uint8'),8)
    total=st[:,4]; cnt=np.bincount(lab[near&ink],minlength=n)
    tight=np.bincount(lab[(dt<=1.5)&ink],minlength=n)
    accept=(cnt>=.965*total)&(tight>=.67*total)&(cnt>0);accept[0]=False
    # Mere proximity is not evidence that a whole glyph is watermark ink.
    # Require the component itself to contain the predicted outline texture.
    # Evaluate small neighborhoods so interrupted rings can still match.
    # A short surviving arc can be clipped by the white halo around a word.
    # Compare the component's own edges, allowing a one-pixel raster offset;
    # nearby document strokes must not dominate that comparison.
    evidence=accept[lab]
    ey,ex=np.where(evidence)
    if not len(ex):return a.copy(),np.zeros_like(a,bool)
    supported=np.zeros(n,np.int64)
    shifts=[(dy,dx) for dy in range(-2,3) for dx in range(-2,3)]
    excess=np.zeros((len(shifts),n),np.float64)
    for left in range(ex.min(),ex.max()+1,96):
        right=left+96; sy=ey[(ex>=left)&(ex<right)]
        if not len(sy):continue
        lo=max(0,sy.min()-12);hi=min(len(a),sy.max()+13)
        l=max(0,left-12);r=min(a.shape[1],right+12)
        gx=(255-a[lo:hi,l:r]).astype('float32')/255
        q=(255-template[lo:hi,l:r]).astype('float32')/255
        def avg(v):return cv.boxFilter(v,-1,(5,5))
        u=avg(gx);vv=np.maximum(0,avg(gx*gx)-u*u);best=np.zeros_like(gx)
        for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,-1),(1,-1),(-1,1)):
            qt=np.roll(q,(dy,dx),(0,1));v=avg(qt)
            den=np.sqrt(vv*np.maximum(0,avg(qt*qt)-v*v))
            corr=(avg(gx*qt)-u*v)/(den+.00001)
            score=(corr>.78)&(den>.015)&(np.abs(gx-qt)<.32)
            best=np.maximum(best,score)
        region=lab[lo:hi,left:min(right,r)]
        seed=best[:,left-l:min(right,r)-l]>0
        supported+=np.bincount(region[seed],minlength=n)
        inside=evidence[lo:hi,left:min(right,r)]
        ids=region[inside]
        yy,xx=np.nonzero(inside);xx+=left-l
        for k,(dy,dx) in enumerate(shifts):
            sample=q[np.clip(yy+dy,0,q.shape[0]-1),np.clip(xx+dx,0,q.shape[1]-1)]
            # White gaps interrupt an outline wherever it lies behind text.
            # Count unexplained dark ink, not those gaps, against a match.
            error=np.maximum(gx[yy,xx]-sample-.12,0)
            excess[k]+=np.bincount(ids,weights=error,minlength=n)
    accept &= (supported>=.63*total)|((excess.min(axis=0)<.038*total)&(total>=4))
    mark=accept[lab]
    # Follow the edge antialiasing of removed components to the paper, keeping
    # all neighboring document components and their antialiasing intact.
    dm=cv.distanceTransform((~mark).astype('uint8'),cv.DIST_L2,5)
    dk=cv.distanceTransform((~(ink&~mark)).astype('uint8'),cv.DIST_L2,5)
    remove=(dm<=3.6)&(dm<dk)&(dt<6)&(a<255)
    b=a.copy();b[remove]=255
    return b,remove

def refine(a,tm):
    dt=cv.distanceTransform((tm>=248).astype('uint8'),cv.DIST_L2,5)
    y,x=np.where((a<250)&(dt<4))
    if not len(x):return a
    out=a.copy()
    for left in range(max(0,x.min()-15),min(a.shape[1],x.max()+16),90):
        right=min(left+90,a.shape[1]);sy=y[(x>=left-12)&(x<right+12)]
        if not len(sy):continue
        lo=max(0,sy.min()-20);hi=min(a.shape[0],sy.max()+21)
        l=max(0,left-16);r=min(a.shape[1],right+16)
        g=a[lo:hi,l:r];pattern=tm[lo:hi,l:r]; gx=(255-g).astype('float32')/255
        # Only remove fragments with strong local evidence of the same curved
        # or double-edged outline. Estimate small subpixel positional errors.
        seed=np.zeros(g.shape,bool)
        def avg(v):return cv.boxFilter(v,-1,(13,13))
        u=avg(gx);vv=np.maximum(0,avg(gx*gx)-u*u)
        for dy in [-1,0,1]:
            for dx in [-1,0,1]:
                p=cv.warpAffine(pattern,np.float32([[1,0,dx],[0,1,dy]]),(g.shape[1],g.shape[0]),borderValue=255)
                q=(255-p).astype('float32')/255;vq=avg(q)
                denom=np.sqrt(vv*np.maximum(0,avg(q*q)-vq*vq))
                corr=(avg(gx*q)-u*vq)/(denom+.00001)
                seed|=(corr>.75)&(q>.08)&(g<235)&(denom>.019)
        # Remove attached outlines up to their junction with the document.
        # Document ink outside the outline gives explicit stop points.
        loc=dt[lo:hi,l:r];doc=(g<200)&(loc>2.1)
        ddoc=cv.distanceTransform((~doc).astype('uint8'),cv.DIST_L2,5)
        near=cv.dilate(seed.astype('uint8'),np.ones((5,5),'uint8'))>0
        erase=near&(loc<3.2)&(ddoc>1.8)
        block=out[lo:hi,left:right];m=erase[:,left-l:right-l];block[m]=255
    return out
