"""Offline checks run *inside* the frozen app by the release pipeline.

Only synthetic pixels are used. No documents, profiles, or network requests.
"""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from clearpage.restore import neural_restore
from clearpage.restore import text_restore
from clearpage.presets import scan_patterns
from clearpage.presets import screened_scans


def run(base, output):
    screen = screened_scans.profile()['mask'].copy()
    cleaned, details = screened_scans.clean_pixels(screen)
    if not details['matched'] or cleaned.any():
        raise RuntimeError('Packaged dotted Un-Official screen failed its blank-paper check.')
    for height in scan_patterns.GRAY_LAYOUTS:
        with np.load(base/f'assets/unofficial-copy-{height}.npz', allow_pickle=False) as asset:
            seed = asset['seed'].copy()
        pixels, mask, stats = scan_patterns.unofficial_copy(Image.fromarray(seed), base/'assets')
        if not stats['matched'] or not mask.any() or not np.array_equal(pixels[~mask], seed[~mask]):
            raise RuntimeError('Packaged Unofficial Copy preset failed its preservation check.')
    image = np.full((64, 128, 3), 255, dtype=np.uint8)
    image[28:36, 60:68] = 175
    mask = np.zeros(image.shape[:2], dtype=bool)
    mask[26:38, 58:70] = True
    result = neural_restore.inpaint(image, mask, base/'models/big-lama.pt')
    if result.shape != image.shape or not np.array_equal(result[~mask], image[~mask]):
        raise RuntimeError('Packaged LaMa did not preserve pixels outside the mask.')
    word = Image.new('RGB', (160, 48), 'white')
    ImageDraw.Draw(word).text((5, 8), 'RELEASE TEST', fill='black')
    prediction = text_restore.recognize(np.array(word), base/'models'/text_restore.MODEL_NAME)
    if not np.isfinite(prediction['confidence']):
        raise RuntimeError('Packaged ONNX recognition returned invalid output.')
    Path(output).write_text(json.dumps({'lama_inference': True, 'onnx_inference': True, 'scan_pattern_assets': True, 'screened_scan_assets': True,
                                      'outside_mask_preserved': True}), encoding='utf-8')
