"""Locate and align intact scanned words for local document restoration.

OCR is used only to retrieve exact text matches. Surviving image strokes must
also agree; names, digits, handwriting and ambiguous matches stay for review.
"""
from collections import defaultdict
import re
from functools import lru_cache
import cv2
import numpy as np


def words(ocr, shape):
    height,width=shape
    result=[]
    for line in ocr.get('lines',[]):
        for item in line.get('words',[]):
            try:
                x,y,w,h=[int(round(float(item[k]))) for k in ('x','y','w','h')]
                text=item['text']
            except (KeyError,TypeError,ValueError,OverflowError):
                continue
            if isinstance(text,str) and 0<=x<x+w<=width and 0<=y<y+h<=height and w>0 and h>0:
                result.append(dict(text=text,x=x,y=y,w=w,h=h))
    return result


def word_box(word, margin=0):
    return [word['x']-margin,word['y']-margin,word['x']+word['w']+margin,word['y']+word['h']+margin]


COMMON_CAPITALS={'The','This','That','If','In','As','Of','For','To','Beginning','Except','EXCEPTING','EXCEPT','Owner','Township','County','State','Section','Range','North','South','East','West'}


def eligible(word):
    # Proper names and numeric strings are not automatically reconstructed.
    tokens=word['text'].split()
    return (1<=len(tokens)<=3 and all(re.fullmatch(r'[a-z]{2,28}[,.;:]?',t) is not None or t.rstrip('.,;:') in COMMON_CAPITALS for t in tokens)
            and 7<=word['h']<=48 and 6<=word['w']<=450)


def spans(ocr,shape):
    all_words=words(ocr,shape);result=list(all_words)
    for line in ocr.get('lines',[]):
        items=words({'lines':[line]},shape)
        for length in (2,3):
            for start in range(len(items)-length+1):
                group=items[start:start+length]
                if any(not eligible(w) for w in group):continue
                if any(b['x']-a['x']-a['w']<0 or b['x']-a['x']-a['w']>max(a['h'],b['h'])*.7 for a,b in zip(group,group[1:])):continue
                x=min(w['x'] for w in group);y=min(w['y'] for w in group)
                xx=max(w['x']+w['w'] for w in group);yy=max(w['y']+w['h'] for w in group)
                item=dict(text=' '.join(w['text'] for w in group),x=x,y=y,w=xx-x,h=yy-y)
                if eligible(item):result.append(item)
    return result


def normalized(text):
    return re.sub('[^a-z]','',text.lower())


def distance(a,b):
    # Bit-parallel edit distance for short OCR strings.
    if not a:return len(b)
    masks={c:sum(1<<i for i,x in enumerate(a) if x==c) for c in set(a)}
    positive=~0;negative=0;score=len(a);top=1<<(len(a)-1)
    for char in b:
        equal=masks.get(char,0);x=equal|negative
        d=(((equal&positive)+positive)^positive)|equal
        hp=negative|~(d|positive);hn=positive&d
        if hp&top:score+=1
        if hn&top:score-=1
        hp=(hp<<1)|1;hn<<=1
        positive=hn|~(x|hp);negative=x&hp
    return score


def make_index(pages):
    index=defaultdict(list)
    for number,gray,mask,ocr in pages:
        excluded=cv2.dilate(mask.astype('uint8'),np.ones((5,5),'uint8'))>0
        for word in spans(ocr,gray.shape):
            x,y,xx,yy=word_box(word,2)
            if not eligible(word) or x<0 or y<0 or xx>gray.shape[1] or yy>gray.shape[0] or excluded[y:yy,x:xx].any():
                continue
            if len(index[word['text']])<70:
                index[word['text']].append(dict(page=number,word=word,size=[gray.shape[1],gray.shape[0]],ink=gray[y:yy,x:xx].copy()))
    return index


def align(gray,excluded,target,donor):
    x,y,xx,yy=word_box(target)
    rw,rh=donor['word']['w'],donor['word']['h']
    if abs(rw/target['w']-1)>.23 or abs(rh/target['h']-1)>.30:
        return None
    x0,y0=max(0,x-7),max(0,y-7)
    x1,y1=min(gray.shape[1],xx+7),min(gray.shape[0],yy+7)
    target_ink=(255-gray[y0:y1,x0:x1].astype('float32'))/255
    valid=(~excluded[y0:y1,x0:x1]).astype('float32')
    known=float((target_ink*valid).sum())
    if known<12:
        return None
    donor_ink=(255-donor['ink'].astype('float32'))/255
    best=None
    for w in sorted({rw+4,target['w']+3,target['w']+4,target['w']+5}):
        for h in sorted({rh+4,target['h']+3,target['h']+4,target['h']+5}):
            if w>target_ink.shape[1] or h>target_ink.shape[0]:continue
            ink=cv2.resize(donor_ink,(w,h),interpolation=cv2.INTER_CUBIC).clip(0,1)
            numerator=cv2.matchTemplate(target_ink*valid,ink,cv2.TM_CCORR)
            a=cv2.matchTemplate(target_ink*target_ink*valid,np.ones_like(ink),cv2.TM_CCORR)
            b=cv2.matchTemplate(valid,ink*ink,cv2.TM_CCORR)
            covered=cv2.matchTemplate(target_ink*valid,np.ones_like(ink),cv2.TM_CCORR)
            score=numerator/np.sqrt(np.maximum(a*b,1e-8))
            score[(covered<known*.90)|(b<10)|(b<.42*float((ink*ink).sum()))]=-1
            _,value,_,pos=cv2.minMaxLoc(score)
            if best is None or value>best['score']:
                best=dict(score=float(value),box=[x0+pos[0],y0+pos[1],x0+pos[0]+w,y0+pos[1]+h],
                          gray=np.clip(255*(1-ink),0,255).astype('uint8'))
    return best


def proposals(gray,current,mask,source_ocr,clean_ocr,index):
    if not mask.any():return [],[]
    excluded=cv2.dilate(mask.astype('uint8'),np.ones((5,5),'uint8'))>0
    targets=spans(clean_ocr,gray.shape)+spans(source_ocr,gray.shape)
    by_length=defaultdict(list)
    for spelling in index:by_length[len(normalized(spelling))].append(spelling)
    @lru_cache(maxsize=1000)
    def similar(text):
        normal=normalized(text)
        if len(normal)<5:return [text] if text in index else []
        limit=1 if len(normal)<12 else 2
        matches=[]
        for length in range(len(normal)-limit,len(normal)+limit+1):
            for spelling in by_length[length]:
                if distance(normal,normalized(spelling))<=limit:matches.append(spelling)
        return matches
    candidates=[];seen=set();review=[]
    for target in targets:
        x,y,xx,yy=word_box(target)
        if not mask[y:yy,x:xx].any():continue
        key=(target['text'],x//3,y//3)
        if key in seen:continue
        seen.add(key)
        core=normalized(target['text'])
        name_like=target['text'][:1].isupper() and target['text'].strip('.,;:') not in COMMON_CAPITALS
        if any(c.isdigit() for c in target['text']) or name_like or not 2<=len(core)<=45 or not 7<=target['h']<=48:
            review.append(dict(text=target['text'],box=[x,y,xx,yy]));continue
        refs=[donor for spelling in similar(target['text']) for donor in index[spelling]]
        refs=sorted(refs,key=lambda d:abs(d['word']['w']/target['w']-1)+abs(d['word']['h']/target['h']-1))[:36]
        best=None;alternatives=[]
        for donor in refs:
            fit=align(gray,excluded,target,donor)
            if fit and fit['score']>=.92:
                alternatives.append((fit['score'],donor['word']['text']))
                if best is None or fit['score']>best['score']:
                    db=word_box(donor['word'],2);dw,dh=donor['size']
                    best=dict(fit,text=donor['word']['text'],ocr_text=target['text'],donor_page=donor['page'],donor_box=word_box(donor['word']),
                              donor_rect=[db[0]/dw,db[1]/dh,db[2]/dw,db[3]/dh],needs_confirmation=donor['word']['text']!=target['text'])
        if best and (best['score']<(.945 if normalized(best['text'])!=core else .93) or any(t!=best['text'] and best['score']-s<.035 for s,t in alternatives)):
            best=None
        if best:
            bx,by,bxx,byy=best['box'];old=current[by:byy,bx:bxx]
            selected=excluded[by:byy,bx:bxx]
            # Require a visible defect within the watermark corridor, rather
            # than replacing every already readable occurrence.
            improvement=int((((old.astype('int16')-best['gray'].astype('int16'))>90)|
                             ((best['gray'].astype('int16')-old.astype('int16'))>90))[selected].sum())
            if improvement>=5:
                # Clear the complete OCR word extent as well as the aligned
                # glyph extent; otherwise damaged tails survive beside a word.
                rx,ry=max(0,min(bx,x-1)),max(0,min(by,y-1))
                rxx,ryy=min(gray.shape[1],max(bxx,xx+1)),min(gray.shape[0],max(byy,yy+1))
                paper=int(np.percentile(current[ry:ryy,rx:rxx],95))
                patch=np.full((ryy-ry,rxx-rx),paper,'uint8')
                patch[by-ry:byy-ry,bx-rx:bxx-rx]=best['gray']
                best['gray']=patch;best['box']=[rx,ry,rxx,ryy]
                best['changed_ink']=improvement;candidates.append(best)
        else:review.append(dict(text=target['text'],box=[x,y,xx,yy]))
    candidates.sort(key=lambda c:c['score'],reverse=True)
    occupied=np.zeros(mask.shape,bool);accepted=[]
    for item in candidates:
        x,y,xx,yy=item['box']
        if occupied[y:yy,x:xx].any():continue
        # Do not choose between two plausible spellings at the same position.
        conflict=any(c['text']!=item['text'] and item['score']-c['score']<.035
                     and max(x,c['box'][0])<min(xx,c['box'][2]) and max(y,c['box'][1])<min(yy,c['box'][3])
                     for c in candidates)
        if conflict:continue
        occupied[y:yy,x:xx]=True;accepted.append(item)
    pending=[];seen_boxes=[]
    for item in review:
        x,y,xx,yy=item['box']
        if occupied[y:yy,x:xx].any():continue
        if any(max(x,b[0])<min(xx,b[2]) and max(y,b[1])<min(yy,b[3]) for b in seen_boxes):continue
        seen_boxes.append(item['box']);pending.append(item)
    return accepted,pending


def background_patch(rgb,mask,ocr):
    """Remove isolated flecks outside whole text-line bands, including missed words."""
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    corridor=cv2.dilate(mask.astype('uint8'),np.ones((5,5),'uint8'))>0
    protected=np.zeros(mask.shape,bool);bands=np.zeros(mask.shape,bool)
    height,width=mask.shape
    for word in words(ocr,mask.shape):
        x,y,xx,yy=word_box(word,2)
        protected[max(0,y):min(height,yy),max(0,x):min(width,xx)]=True
    for line in ocr.get('lines',[]):
        items=words({'lines':[line]},mask.shape)
        reliable=[w for w in items if len(w['text'])>=2 and 7<=w['h']<=40 and not corridor[w['y']:w['y']+w['h'],w['x']:w['x']+w['w']].any()]
        if len(reliable)<4:continue
        top=min(w['y'] for w in reliable);bottom=max(w['y']+w['h'] for w in reliable)
        if bottom-top>45:continue
        left=min(w['x'] for w in items);right=max(w['x']+w['w'] for w in items)
        bands[max(0,top-8):min(height,bottom+8),max(0,left-2):min(width,right+2)]=True
        protected[max(0,top-4):min(height,bottom+4),max(0,left-8):min(width,right+8)]=True
    _,labels,stats,_=cv2.connectedComponentsWithStats((gray<210).astype('uint8'),connectivity=8)
    touching=np.unique(labels[(gray<210)&~corridor]);touching=touching[touching!=0]
    # Dots, commas and other small punctuation can be missed by OCR.
    small=np.flatnonzero(stats[:,cv2.CC_STAT_AREA]<=24);small=small[small!=0]
    touching=np.union1d(touching,small)
    protected|=cv2.dilate(np.isin(labels,touching).astype('uint8'),np.ones((3,3),'uint8'))>0
    erase=corridor&bands&~protected&(gray<250)
    paper=cv2.medianBlur(np.where(rgb>230,rgb,255).astype('uint8'),9)
    indices=np.flatnonzero(erase).astype('uint32')
    return dict(indices=indices,pixels=paper.reshape(-1,3)[indices])
