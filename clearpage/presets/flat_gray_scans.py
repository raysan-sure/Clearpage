"""Exact black-pixel preservation for repeated light-gray marks on binary scans.

This is deliberately not a general grayscale threshold: only neutral scans with
no levels between black and 211, a dominant 211 watermark level, and repeated
horizontal watermark bands qualify. Work on native samples, never a rendering.
"""
from pathlib import Path
import json
import numpy as np
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
from clearpage.pdf.pdf_overlays import read_pdf
from clearpage.presets.dotted_scans import replace_image
from clearpage.presets import tammany_scans

MAX_PIXELS = 12_000_000
CACHE = 'native-flat-gray-v2'


def unchanged_details(note):
    return dict(scan_pattern=True, confidence=None, matched=False, changed=0,
                protected=0, reviewed=False, uncertain=False, ai=False, pattern_note=note)


def clean(image, cancelled=lambda: False):
    if cancelled(): raise InterruptedError('Cancelled')
    if image.width*image.height > MAX_PIXELS:
        raise ValueError('This scan is too large for light-gray cleanup.')
    a=np.array(image.convert('RGB'));g=a[:,:,0]
    empty=np.zeros(g.shape,bool)
    details=unchanged_details('Page kept unchanged: this preset requires pure black ink and a separate repeated light-gray watermark. Use another preset for ordinary grayscale scans.')
    if np.any(a!=g[:,:,None]) or np.any((g>0)&(g<211)):
        return a,empty,details
    mask=(g>=211)&(g<255)
    count=int(mask.sum())
    if count<100 or int((g==211).sum())<count*.5:
        return a,empty,details
    # Require widely separated horizontal watermark rows, not a gray logo,
    # a single light annotation, or a filled background rectangle.
    rows=np.flatnonzero(mask.sum(axis=1)>max(8,g.shape[1]*.015))
    if not len(rows):return a,empty,details
    groups=np.split(rows,np.flatnonzero(np.diff(rows)>max(3,g.shape[0]*.025))+1)
    if len(groups)<3 or groups[-1][0]-groups[0][-1]<g.shape[0]*.45:
        return a,empty,details
    if cancelled():raise InterruptedError('Cancelled')
    result=a.copy();result[mask]=255
    assert np.array_equal(result[g==0],a[g==0])
    details.update(matched=True,changed=count,protected=int((g==0).sum()),
                   black_pixels_exact=True,uncertain=True,
                   pattern_note='Repeated light-gray marks removed at the original scan resolution. Every original black pixel is unchanged; no words were rewritten. Check that gray marks were watermarks rather than document content.')
    return result,mask,details


def scan_object(page):
    resources=page.get('/Resources',{})
    objects=resources.get('/XObject',{})
    if len(objects)!=1:return None
    name=next(iter(objects));obj=objects[name].get_object()
    if (obj.get('/Subtype')!='/Image' or obj.get('/ColorSpace')!='/DeviceRGB'
            or obj.get('/BitsPerComponent')!=8):return None
    width,height=int(obj.get('/Width',0)),int(obj.get('/Height',0))
    if min(width,height)<100 or width*height>MAX_PIXELS:return None
    if any(k in obj for k in ('/Mask','/SMask','/ImageMask','/Decode')):return None
    content=page.get_contents()
    if content is None:return None
    draws=[args for args,op in content.operations if op==b'Do']
    if draws!=[[name]]:return None
    # The image may be offset, scaled, cropped or on a rotated page. Replacing
    # its samples preserves all placement commands and other page contents.
    return name,obj


def prepare(source,index,directory,cancelled=lambda:False):
    if cancelled():raise InterruptedError('Cancelled')
    target=Path(directory)/f'{index}-{CACHE}.pdf';metadata=target.with_suffix('.json')
    if target.exists() and metadata.exists():return target,json.loads(metadata.read_text())
    reader=read_pdf(source)
    page=reader.pages[index]
    tammany=tammany_scans.scan_object(page)
    found=tammany or scan_object(page)
    if found is None:return None
    name,obj=found
    if tammany:
        pixels=tammany_scans.pixels(tammany)
        mark=tammany_scans.detected_stamp(pixels)
        details=unchanged_details('No supported gray watermark found on this scan.')
        if mark is not None:
            result=tammany_scans.clean(reader,pixels,mark,directory,cancelled)
            changed=result!=pixels
            if pixels.ndim==3:
                changed=changed.any(axis=2)
            details.update(matched=True,changed=int(changed.sum()),native_tammany=True,
                           black_pixels_exact=True,uncertain=pixels.ndim==3,
                           pattern_note='St. Tammany gray watermark removed at the original scan resolution. Black ink and colored seals are preserved. Review the cover page where the stamp crossed text.')
    else:
        image=page.images[0].image.convert('RGB')
        if image.size!=(int(obj['/Width']),int(obj['/Height'])):
            raise ValueError('The decoded scan dimensions do not match the PDF.')
        result,changed,details=clean(image,cancelled)
    writer=PdfWriter();writer.add_page(page)
    if details['changed']:
        cloned=writer.pages[0]['/Resources']['/XObject'][name].get_object()
        stream=DecodedStreamObject()
        stream.update({k:v for k,v in cloned.items() if k not in ('/Filter','/DecodeParms','/Length')})
        stream.set_data(result.tobytes())
        replace_image(writer,writer.pages[0],name,stream.flate_encode())
    details['native_flat_gray']=True
    temporary=target.with_suffix('.part')
    try:
        if cancelled():raise InterruptedError('Cancelled')
        with temporary.open('wb') as output:writer.write(output)
        if cancelled():raise InterruptedError('Cancelled')
        temporary.replace(target);metadata.write_text(json.dumps(details))
    finally:temporary.unlink(missing_ok=True)
    return target,details


def write_clean(source,target,keep,directory,cancelled=lambda:False):
    writer=PdfWriter();writer.clone_document_from_reader(read_pdf(source))
    tammany_changed=False
    for index in keep:
        if cancelled():raise InterruptedError('Cancelled')
        path=Path(directory)/f'{index}-{CACHE}.pdf'
        details=json.loads(path.with_suffix('.json').read_text())
        if not details['changed']:continue
        cached=read_pdf(path).pages[0]
        if details.get('native_tammany'):
            found=tammany_scans.scan_object(cached)
            original=tammany_scans.scan_object(writer.pages[index])
            if found is None or original is None or found[0]!=original[0]:
                raise ValueError('The St. Tammany scan cache is invalid. Process again.')
            tammany_changed=True
        else:
            found=scan_object(cached)
        if found is None:raise ValueError('The light-gray scan cache is invalid. Process again.')
        name,obj=found;replace_image(writer,writer.pages[index],name,obj.clone(writer))
    if tammany_changed:
        writer.compress_identical_objects(remove_duplicates=False,remove_unreferenced=True)
    temporary=Path(target).with_suffix('.part')
    try:
        if cancelled():raise InterruptedError('Cancelled')
        with temporary.open('wb') as output:writer.write(output)
        if cancelled():raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
