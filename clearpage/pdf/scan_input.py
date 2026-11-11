"""Bounded, local JPEG/PNG/TIFF import into a lossless-image PDF container."""
from pathlib import Path
import math
import warnings
from PIL import Image, ImageOps
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from clearpage.pdf.engine import MAX_PAGES, MAX_PIXELS, SCALE, inspect_pdf

MAX_TOTAL_PIXELS = 120_000_000


def import_scan(source, target):
    target = Path(target)
    partial = target.with_suffix('.part')
    pages = 0
    total = 0
    fallback = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as document:
                if document.format not in ('JPEG', 'PNG', 'TIFF'):
                    raise ValueError('Choose a PDF, JPG, PNG or TIFF scan.')
                count = getattr(document, 'n_frames', 1)
                if not 1 <= count <= MAX_PAGES or (document.format != 'TIFF' and count != 1):
                    raise ValueError('Use a still JPG/PNG or a TIFF with 1 to 300 pages.')
                kind = document.format
                canvas = Canvas(str(partial), pageCompression=1)
                for index in range(count):
                    document.seek(index)
                    width, height = document.size
                    total += width*height
                    if width*height > MAX_PIXELS or total > MAX_TOTAL_PIXELS:
                        raise ValueError('Scans support up to 12 million pixels per page and 120 million pixels per file. Split a large TIFF into smaller files.')
                    dpi = document.info.get('dpi', (144, 144))
                    try:
                        dx, dy = map(float, dpi)
                        if not all(math.isfinite(v) and 36 <= v <= 1200 for v in (dx, dy)):raise ValueError()
                    except (ValueError, TypeError, OverflowError):
                        dx = dy = 144; fallback = True
                    if 'dpi' not in document.info: fallback = True
                    orientation = document.getexif().get(274, 1)
                    image = ImageOps.exif_transpose(document)
                    if orientation in (5,6,7,8):dx,dy=dy,dx
                    if 'A' in image.getbands() or 'transparency' in image.info:
                        rgba=image.convert('RGBA'); rgb=Image.new('RGB',rgba.size,'white');rgb.paste(rgba,mask=rgba.getchannel('A'))
                    else:rgb=image.convert('RGB')
                    # Align the PDF page to the 144-DPI processing grid. PNG pHYs
                    # often reports 144.018 rather than 144 DPI; fractional page
                    # extents would resample a full-page correction a second time.
                    w,h=round(rgb.width*72/dx*SCALE)/SCALE,round(rgb.height*72/dy*SCALE)/SCALE
                    if not (1<=w<=4000 and 1<=h<=4000) or w*h*SCALE*SCALE>MAX_PIXELS:
                        raise ValueError('The scan print size is too large. Set a valid scan DPI or use a smaller image.')
                    canvas.setPageSize((w,h))
                    # Feed decoded pixels, avoiding a second JPEG compression pass.
                    canvas.drawImage(ImageReader(rgb),0,0,width=w,height=h)
                    canvas.showPage();pages+=1
                canvas.save()
        inspect_pdf(partial)
        partial.replace(target)
        return dict(input_type=kind, input_note=f'{pages} scanned page(s) imported. '+
                    ('Missing or invalid DPI uses 144 DPI. ' if fallback else 'Scan DPI used for page size. ')+
                    'Page sizes rounded to the nearest half point. '+
                    'Exports are PDFs; transparent areas use white paper. Processing previews use 144 DPI.')
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ValueError('This scan is too large to decode safely.') from error
    except (OSError, SyntaxError) as error:
        raise ValueError('This image is damaged or unsupported. Choose a valid JPG, PNG or TIFF scan.') from error
    finally:
        partial.unlink(missing_ok=True)
