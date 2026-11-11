"""Offline LaMa inference, with bounded tiles and exact outside-mask preservation.

LaMa: advimman/lama, Apache-2.0. TorchScript artifact published by Sanster.
RGB normalization and 8-pixel padding follow IOPaint and D-Ogi's adapter.
ClearPage adds disjoint output tiles, cancellation and finite-output checks.
"""
import hashlib
from pathlib import Path
import threading
import numpy as np

MODEL_SHA256 = '344c77bbcb158f17dd143070d1e789f38a66c04202311ae3a258ef66667a9ea9'
MODEL_SIZE = 205669692
_models = {}
_lock = threading.RLock()


def verify_model(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        raise ValueError('LaMa weights are missing. Use the complete ClearPage AI Windows package.')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != MODEL_SHA256:
            raise ValueError('LaMa weights failed verification. Replace the model with the published package.')


def predictor(path):
    with _lock:
        key = str(Path(path).resolve())
        if key not in _models:
            verify_model(path)
            try:
                import torch
            except ImportError as exc:
                raise ValueError('The local AI runtime is unavailable. Use the AI Windows package.') from exc
            torch.set_num_threads(4)
            with Path(path).open('rb') as stream:
                # Recheck the same handle loaded below; no unverified pickle inputs.
                if hashlib.file_digest(stream, 'sha256').hexdigest() != MODEL_SHA256:
                    raise ValueError('LaMa weights changed during verification.')
                stream.seek(0)
                _models[key] = torch.jit.load(stream, map_location='cpu').eval()
        model = _models[key]

    def predict(image, mask):
        import torch
        with _lock, torch.inference_mode():
            rgb = torch.from_numpy(np.ascontiguousarray(image.transpose(2,0,1))).float()[None] / 255.
            selected = torch.from_numpy((mask>0).astype('int64'))[None,None]
            output = model(rgb, selected)
            if tuple(output.shape) != tuple(rgb.shape) or not torch.isfinite(output).all():
                raise ValueError('The AI model returned invalid pixels. No changes were applied.')
            return np.clip(output[0].permute(1,2,0).numpy()*255,0,255).astype('uint8')
    return predict


def inpaint(image, mask, model_path=None, *, predict=None, cancelled=lambda: False, progress=lambda n,total: None):
    if not isinstance(image,np.ndarray) or image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3:
        raise ValueError('AI input must be an RGB image.')
    if not isinstance(mask,np.ndarray) or mask.shape!=image.shape[:2] or mask.dtype not in (np.bool_,np.uint8):
        raise ValueError('The AI mask must match the page.')
    h,w=mask.shape
    if not h or not w or h*w>12_000_000:
        raise ValueError('This page is too large for local AI processing.')
    selected=mask>0;result=image.copy()
    if not selected.any():return result
    if cancelled():raise InterruptedError('AI cleanup stopped.')
    predict=predict or predictor(model_path)
    # Predictions see the unchanged source. Disjoint write regions prevent one
    # overlapping context crop from undoing another's work. No page downsampling.
    tile,margin=384,64
    tiles=[(x,y,min(x+tile,w),min(y+tile,h)) for y in range(0,h,tile) for x in range(0,w,tile)
           if selected[y:min(y+tile,h),x:min(x+tile,w)].any()]
    for number,(x,y,xx,yy) in enumerate(tiles):
        if cancelled():raise InterruptedError('AI cleanup stopped.')
        progress(number+1,len(tiles))
        l,t,r,b=max(0,x-margin),max(0,y-margin),min(w,xx+margin),min(h,yy+margin)
        crop=image[t:b,l:r];cut=selected[t:b,l:r];ch,cw=cut.shape
        padding=((0,max(32,((ch+7)//8)*8)-ch),(0,max(32,((cw+7)//8)*8)-cw))
        padded=np.pad(crop,(*padding,(0,0)),mode='symmetric')
        generated=predict(padded,np.pad(cut.astype('uint8')*255,padding,mode='symmetric'))
        if not isinstance(generated,np.ndarray) or generated.shape!=padded.shape or generated.dtype!=np.uint8:
            raise ValueError('The AI model returned invalid pixels. No changes were applied.')
        output=generated[y-t:yy-t,x-l:xx-l];scope=selected[y:yy,x:xx]
        result[y:yy,x:xx][scope]=output[scope]
    if cancelled():raise InterruptedError('AI cleanup stopped.')
    return result
