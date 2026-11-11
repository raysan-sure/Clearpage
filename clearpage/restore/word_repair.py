"""Local, explicitly confirmed word reconstruction. No inferred document wording."""
import math
import os
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont
import numpy as np


def rectangle(value, size):
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError('Draw a rectangle around one word, including its full letter height.')
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value):
        raise ValueError('Invalid word selection.')
    x0, y0, x1, y1 = value
    width, height = size
    box = (round(x0*width), round(y0*height), round(x1*width), round(y1*height))
    w, h = box[2]-box[0], box[3]-box[1]
    if w < 4 or h < 5 or h > height*.08 or w*h > width*height*.025:
        raise ValueError('Select a single word or short phrase, with a small margin around its letters.')
    return box


def confirmed_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise ValueError('Enter the exact confirmed wording (up to 80 characters, one line).')
    return value.strip()


def paper_color(image, box):
    x0, y0, x1, y1 = box
    array = np.asarray(image.convert('RGB'))
    rim = array[max(0,y0-3):min(image.height,y1+3), max(0,x0-3):min(image.width,x1+3)]
    # Bright paper samples avoid importing nearby black text into the fill.
    bright = rim.mean(axis=2) >= np.percentile(rim.mean(axis=2), 75)
    return np.median(rim[bright], axis=0).astype('uint8')


def make_patch(image, box, data, donor=None):
    text = confirmed_text(data.get('text'))
    w, h = box[2]-box[0], box[3]-box[1]
    if data.get('method') == 'donor':
        if donor is None:
            raise ValueError('Select a clean occurrence on the original page, or use Find matching word.')
        donor_box = rectangle(data.get('donor_rect'), donor.size)
        cut = donor.crop(donor_box).convert('RGB')
        # Copy authentic scanned pixels, including their original antialiasing.
        patch = cut.resize((w, h), Image.Resampling.LANCZOS)
        return patch, 'Copied a selected clean occurrence; confirm that its spelling and size match.'
    if data.get('method') != 'text':
        raise ValueError('Choose a word repair method.')
    fonts = {'serif':'times.ttf', 'serif-bold':'timesbd.ttf', 'serif-italic':'timesi.ttf',
             'sans':'arial.ttf', 'sans-bold':'arialbd.ttf', 'mono':'cour.ttf'}
    choice = data.get('font', 'serif')
    if choice not in fonts:
        raise ValueError('Choose an available font.')
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/fonts[choice]
    if not font_path.is_file():
        raise ValueError('This Windows font is unavailable. Choose a different font or copy a clean occurrence.')
    if not text.isascii():
        raise ValueError('Typed repair currently supports English text. Use a clean occurrence for other scripts.')
    scale = 4
    font = ImageFont.truetype(str(font_path), 96*scale)
    bounds = font.getbbox(text)
    glyph = Image.new('L', (max(1,bounds[2]-bounds[0]), max(1,bounds[3]-bounds[1])), 0)
    ImageDraw.Draw(glyph).text((-bounds[0],-bounds[1]), text, font=font, fill=255)
    margin = max(1, round(h*.07))
    glyph = glyph.resize((max(1,w-2*margin)*scale, max(1,h-2*margin)*scale), Image.Resampling.LANCZOS)
    opacity = data.get('darkness', 100)
    if type(opacity) not in (float,int) or not math.isfinite(opacity) or not 40 <= opacity <= 100:
        raise ValueError('Ink strength must be between 40 and 100 percent.')
    mask = Image.new('L', (w*scale,h*scale))
    mask.paste(glyph, (margin*scale,margin*scale))
    mask = mask.resize((w,h), Image.Resampling.LANCZOS)
    paper = paper_color(image, box)
    alpha = np.asarray(mask,dtype=float)[...,None]/255*(opacity/100)
    patch = np.clip(paper*(1-alpha),0,255).astype('uint8')
    return Image.fromarray(patch), 'Reconstructed from your confirmed wording. Review font, letter spacing and alignment.'


def exact_matches(ocr, text, size, excluded=None):
    """Return exact, case-sensitive OCR matches; never propose corrected wording."""
    wanted = confirmed_text(text).split()
    result = []
    for line in ocr.get('lines', []):
        words = line.get('words', [])
        for start in range(len(words)-len(wanted)+1):
            group = words[start:start+len(wanted)]
            if [w['text'] for w in group] != wanted:
                continue
            x0 = max(0, math.floor(min(w['x'] for w in group))-2)
            y0 = max(0, math.floor(min(w['y'] for w in group))-2)
            x1 = min(size[0], math.ceil(max(w['x']+w['w'] for w in group))+2)
            y1 = min(size[1], math.ceil(max(w['y']+w['h'] for w in group))+2)
            if excluded is not None and excluded[y0:y1,x0:x1].any():
                continue
            result.append([x0/size[0],y0/size[1],x1/size[0],y1/size[1]])
    return result[:30]
