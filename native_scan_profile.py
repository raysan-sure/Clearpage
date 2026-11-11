"""Page-local scan replacement shared by the county profiles."""
import json
import zlib
from pathlib import Path

import pikepdf
from henry_red import save
from hocking_outline import replace_resource


def image_entry(page, bits, colors, filters, bounds):
    objects = page.Resources.get('/XObject', {})
    if len(objects) != 1:
        return None
    name, obj = next(iter(objects.items()))
    if obj.get('/Subtype') != '/Image' or obj.get('/BitsPerComponent') != bits:
        return None
    w, h = int(obj.get('/Width', 0)), int(obj.get('/Height', 0))
    if not bounds(w, h) or str(obj.get('/ColorSpace')) not in colors:
        return None
    codec = obj.get('/Filter')
    if isinstance(codec, pikepdf.Array) and len(codec) == 1:
        codec = codec[0]
    if str(codec) not in filters or any(k in obj for k in ('/Mask', '/SMask', '/ImageMask', '/Decode')):
        return None
    draws = [str(args[0]) for args, op in pikepdf.parse_content_stream(page) if str(op) == 'Do']
    return (name, obj) if draws == [name] else None


def prepare(source, index, directory, cache, processor, cancelled):
    target = Path(directory)/f'{index}-{cache}.pdf'
    meta = target.with_suffix('.json')
    if cancelled():
        raise InterruptedError('Cancelled')
    if target.exists() and meta.exists():
        return target, json.loads(meta.read_text())
    with pikepdf.open(source) as doc, pikepdf.new() as preview:
        preview.pages.append(doc.pages[index])
        result = processor(doc, index, directory, cancelled)
        changed = 0
        if result is not None:
            name, pixels, bits, changed = result
            image = preview.make_stream(zlib.compress(pixels.tobytes()))
            original = preview.pages[0].Resources.XObject[name]
            for key in ('/Type', '/Subtype', '/Width', '/Height', '/Interpolate', '/Intent'):
                if key in original:
                    image[key] = original[key]
            image.Filter = pikepdf.Name.FlateDecode
            image.ColorSpace = pikepdf.Name.DeviceGray
            image.BitsPerComponent = bits
            replace_resource(preview.pages[0], name, image)
        save(preview, target, cancelled)
    details = dict(scan_pattern=True, matched=bool(changed), changed=changed, confidence=None,
                   protected=0, reviewed=False, uncertain=bool(changed),
                   pattern_note=('Review the scan where the stamp crossed ink.' if changed
                                 else 'No matching stamp detected on this page.'))
    meta.write_text(json.dumps(details))
    return target, details


def export_cached(source, target, keep, directory, cache, cancelled):
    with pikepdf.open(source) as doc:
        for index in keep:
            if cancelled():
                raise InterruptedError('Cancelled')
            path = Path(directory)/f'{index}-{cache}.pdf'
            if not json.loads(path.with_suffix('.json').read_text())['changed']:
                continue
            with pikepdf.open(path) as cached:
                page = doc.pages[index]
                names = list(page.Resources.XObject.keys())
                if len(names) != 1 or names != list(cached.pages[0].Resources.XObject.keys()):
                    raise ValueError('The preview no longer matches the page. Process the PDF again.')
                name = names[0]
                image = cached.pages[0].Resources.XObject[name]
                replace_resource(page, name, doc.copy_foreign(image))
        save(doc, target, cancelled)
