"""Offline OCR cross-checks and typography fitted to surviving source ink.

PP-OCRv6 recognition preprocessing/CTC decoding follows RapidOCR (Apache-2.0).
See vendor/README-RapidOCR.txt. Recognition confidence is not document accuracy.
"""
import hashlib
import math
import os
from functools import lru_cache
from pathlib import Path
from threading import RLock

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from clearpage.restore import auto_restore

MODEL_SHA256 = '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884'
MODEL_NAME = 'PP-OCRv6_rec_small.onnx'
_lock = RLock()
_sessions = {}
FONTS = ('times.ttf', 'timesbd.ttf', 'timesi.ttf', 'arial.ttf', 'arialbd.ttf', 'cour.ttf')


def load_recognizer(path):
    path = Path(path).resolve()
    with _lock:
        if not path.is_file():
            raise ValueError('The offline text model is missing. Reinstall the complete ClearPage package.')
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key in _sessions:
            return _sessions[key]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise ValueError('The offline text model failed its integrity check. Reinstall the complete package.')
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise ValueError('The offline OCR runtime is unavailable. Install requirements-ai.txt.') from error
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(data, sess_options=options, providers=['CPUExecutionProvider'])
        chars = ['blank'] + session.get_modelmeta().custom_metadata_map['character'].splitlines() + [' ']
        _sessions.clear()
        _sessions[key] = (session, chars)
        return session, chars


def recognize(rgb, model_path):
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Text recognition needs an RGB image.')
    h, w = rgb.shape[:2]
    if h < 5 or w < 4 or h > 180 or w > 1600 or w / h > 80:
        raise ValueError('Select one word or a short line for text recognition.')
    session, chars = load_recognizer(model_path)
    resized_w = math.ceil(48*w/h)
    data = cv2.resize(rgb[:, :, ::-1], (resized_w, 48)).astype('float32').transpose(2, 0, 1)/127.5-1
    padded = np.zeros((1, 3, 48, max(320, resized_w)), 'float32')
    padded[0, :, :, :resized_w] = data
    with _lock:
        pred = session.run(None, {session.get_inputs()[0].name: padded})[0][0]
    if not np.isfinite(pred).all():
        raise ValueError('Text recognition returned an invalid result.')
    ids = pred.argmax(1)
    selection = np.r_[True, ids[1:] != ids[:-1]] & (ids != 0)
    if not selection.any():
        return dict(text='', confidence=0.)
    return dict(text=''.join(chars[i] for i in ids[selection]),
                confidence=float(pred.max(1)[selection].mean()))


@lru_cache(maxsize=512)
def glyph(text, name, size=192):
    path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/name
    if not path.is_file():
        return None
    font = ImageFont.truetype(str(path), size)
    b = font.getbbox(text)
    image = Image.new('L', (max(1,b[2]-b[0]), max(1,b[3]-b[1])), 0)
    ImageDraw.Draw(image).text((-b[0],-b[1]), text, font=font, fill=255)
    return image


def fit(gray, excluded, box, text, cancelled=lambda: False):
    """Fit a supplied spelling, without using covered pixels as evidence.

    Returns None when too little authentic ink remains. The score measures
    shape similarity only; callers must independently establish the wording.
    """
    x,y,xx,yy = box
    if (not isinstance(text,str) or not text.isascii() or not 1 <= len(text) <= 80
            or any(ord(c)<32 for c in text) or not 0<=x<xx<=gray.shape[1]
            or not 0<=y<yy<=gray.shape[0] or xx-x>700 or yy-y>80):
        return None
    region = gray[y:yy,x:xx]
    valid = (~excluded[y:yy,x:xx]).astype('float32')
    ink = (255-region.astype('float32'))/255
    known = float((ink*valid).sum())
    if known < 12:
        return None
    best = None
    # A two-pixel word margin is included by callers. Search position, font,
    # dimensions and edge weight instead of stretching text to the OCR box.
    for name in FONTS:
        if cancelled():
            raise InterruptedError()
        rendered = glyph(text, name)
        if rendered is None:
            continue
        for w in range(max(5,round((xx-x)*.77)), xx-x+1):
            for h in range(max(7,round((yy-y)*.48)), yy-y+1):
                native = glyph(text,name,max(6,round(192*h/rendered.height)))
                for sample in (rendered,native):
                    g = np.asarray(sample.resize((w,h),Image.Resampling.LANCZOS),dtype='float32')/255
                    for gamma in (.6,1.,1.6):
                        a = g**gamma
                        dot = cv2.matchTemplate(ink*valid,a,cv2.TM_CCORR)
                        aa = cv2.matchTemplate(ink*ink*valid,np.ones_like(a),cv2.TM_CCORR)
                        bb = cv2.matchTemplate(valid,a*a,cv2.TM_CCORR)
                        covered = cv2.matchTemplate(ink*valid,np.ones_like(a),cv2.TM_CCORR)
                        scores = dot/np.sqrt(np.maximum(aa*bb,1e-8))
                        scores[(covered<known*.95)|(bb<10)|(bb<float((a*a).sum())*.5)] = -1
                        _,score,_,position = cv2.minMaxLoc(scores)
                        if best is None or score > best['score']:
                            best = dict(score=float(score),font=name,alpha=a,position=position)
    if not best or best['score'] <= 0:
        return None
    a = best.pop('alpha'); dx,dy = best.pop('position')
    v = valid[dy:dy+a.shape[0],dx:dx+a.shape[1]]
    tone = np.clip(float((ink[dy:dy+a.shape[0],dx:dx+a.shape[1]]*a*v).sum())/
                   max(1e-8,float((a*a*v).sum())),.4,1)
    paper = float(np.percentile(region,95))
    patch = np.full(region.shape,paper,'uint8')
    patch[dy:dy+a.shape[0],dx:dx+a.shape[1]] = np.clip(paper*(1-a*tone),0,255).astype('uint8')
    return dict(best,gray=patch,box=list(box),text=text)


def proposals(source, current, mask, source_ocr, clean_ocr, model_path,
              protected=(), cancelled=lambda: False, vocabulary=None):
    """Conservative automatic fits; different OCR readings remain suggestions."""
    gray = cv2.cvtColor(source,cv2.COLOR_RGB2GRAY)
    excluded = cv2.dilate(mask.astype('uint8'),np.ones((3,3),'uint8'))>0
    occupied = np.zeros(mask.shape,bool)
    for item in protected:
        x,y,xx,yy=item['box'];occupied[y:yy,x:xx]=True
    targets = auto_restore.words(clean_ocr,mask.shape)+auto_restore.words(source_ocr,mask.shape)
    accepted=[];suggestions=[];seen=set();checked=0
    for word in targets:
        if cancelled():raise InterruptedError()
        x,y,xx,yy=auto_restore.word_box(word,2)
        if (x<0 or y<0 or xx>mask.shape[1] or yy>mask.shape[0] or xx-x>400 or yy-y>52
                or yy-y<9 or xx-x<8 or occupied[y:yy,x:xx].any() or not mask[y:yy,x:xx].any()):
            continue
        key=(x//3,y//3,xx//3,yy//3)
        if key in seen:continue
        seen.add(key);checked+=1
        if checked>150:break
        first=recognize(source[y:yy,x:xx],model_path)
        if first['confidence']<.97 or not first['text'].isascii():continue
        text=first['text']
        if not auto_restore.eligible(dict(word,text=text)):continue
        # A changed spelling must also occur in an intact scanned word when
        # the caller has a document dictionary. Do not suggest OCR nonwords.
        if vocabulary is not None and text!=word['text'] and text not in vocabulary:continue
        second=recognize(current[y:yy,x:xx],model_path)
        agreed=(text==word['text'] or (text==second['text'] and second['confidence']>=.97))
        # Two- and three-letter words cannot distinguish similar font families
        # reliably. Keep those fits for review even if both OCR readings agree.
        if len(text.strip('.,;: '))<4:agreed=False
        fitted=fit(gray,excluded,[x,y,xx,yy],text,cancelled)
        if fitted is None or fitted['score']<.94:continue
        diff=np.abs(cv2.cvtColor(current[y:yy,x:xx],cv2.COLOR_RGB2GRAY).astype('int16')-fitted['gray'])
        if int(((diff>70)&excluded[y:yy,x:xx]).sum())<5:continue
        if not agreed:
            suggestions.append(dict(text=word['text'],box=[x,y,xx,yy],suggested_text=text,
                                    suggested_method='fit',shape_score=fitted['score']))
            continue
        occupied[y:yy,x:xx]=True
        accepted.append(dict(fitted,method='font-fit',ocr_text=word['text'],ocr_confidence=first['confidence']))
    return accepted,suggestions
