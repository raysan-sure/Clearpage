"""Bounded adaptation of PureScan's MIT-licensed multiply inversion.

PureScan commit d1e020828133e266238d976dfcb419dbd6778942.
Upstream mathematical core is unchanged in vendor/purescan_core.py.
ClearPage supplies alignment, scope checks and preservation of existing edits.
"""
import numpy as np
import cv2
from PIL import Image
from clearpage.pdf.engine import known_alignment, known_mask
from vendor.purescan_core import extract_template, invert_multiply

MIN_SAMPLES = 6
MAX_SAMPLES = 40


def learn_template(pages, template_path, cancelled=lambda: False, progress=lambda n: None):
    """Stream aligned page crops; never stack full document pages in memory."""
    seed = np.load(template_path, allow_pickle=False)
    maximum = None
    samples = 0
    for number, image in pages:
        if cancelled():
            raise InterruptedError('Template learning cancelled.')
        progress(number)
        match, score = known_alignment(image, template_path, min_score=.60)
        if match is None:
            continue
        x,y,q = match['x'],match['y'],match['template']
        rotated=image.convert('RGB').rotate(-45,resample=Image.Resampling.BICUBIC,expand=True,fillcolor='white')
        crop=np.array(rotated.crop((x,y,x+q.shape[1],y+q.shape[0])).resize((seed.shape[1],seed.shape[0]),Image.Resampling.BICUBIC))
        maximum=crop.astype('float64') if maximum is None else extract_template(np.stack((maximum,crop)))
        samples += 1
        if samples >= MAX_SAMPLES:
            break
    if cancelled():
        raise InterruptedError('Template learning cancelled.')
    if samples < MIN_SAMPLES:
        return dict(template=None,samples=samples,reason=f'PureScan needs at least {MIN_SAMPLES} confidently aligned watermark samples; found {samples}. Careful cleanup retained.')
    # Restrict the learned projection to the known watermark, never repeated headers.
    support=cv2.dilate((seed<237).astype('uint8'),np.ones((3,3),'uint8'))>0
    maximum[~support]=255
    if np.count_nonzero(np.any(maximum<245,axis=2))<50:
        return dict(template=None,samples=samples,reason='No stable blended watermark template was learned. Careful cleanup retained.')
    return dict(template=maximum.astype('uint8'),samples=samples,reason='')


def guarded_invert(source, current, template, scope):
    """Preserve prior corrections, near-black ink, and unstable near-opaque pixels."""
    stable=np.min(template,axis=2)>=64
    selected=scope & stable & np.any(template<250,axis=2)
    selected &= np.all(source==current,axis=2)
    selected &= np.min(source,axis=2)>32
    result=invert_multiply(source,template,selected)
    output=current.copy()
    output[selected]=result[selected]
    return output,int(np.any(output!=current,axis=2).sum())


def apply(image, current, model, template_path):
    if model.get('template') is None:
        return current,dict(inverted=0,inversion_samples=model.get('samples',0),inversion_note=model.get('reason','Template unavailable.'))
    match,score=known_alignment(image,template_path,min_score=.35)
    if match is None:
        return current,dict(inverted=0,inversion_samples=model['samples'],inversion_note='No confident watermark alignment; PureScan skipped.')
    x,y,q=match['x'],match['y'],match['template']
    height,width=match['rotated_shape']
    canvas=Image.new('RGB',(width,height),'white')
    canvas.paste(Image.fromarray(model['template']).resize((q.shape[1],q.shape[0]),Image.Resampling.BICUBIC),(x,y))
    back=canvas.rotate(45,resample=Image.Resampling.BICUBIC,expand=False,fillcolor='white')
    left,top=(back.width-image.width)//2,(back.height-image.height)//2
    template=np.array(back.crop((left,top,left+image.width,top+image.height)))
    scope,_=known_mask(image,template_path,min_score=.35)
    result,changed=guarded_invert(np.array(image.convert('RGB')),current,template,scope)
    return result,dict(inverted=changed,inversion_samples=model['samples'],inversion_note='PureScan reverse blending applied. Opaque marks and hidden lettering still require review.')
