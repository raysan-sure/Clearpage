"""Conservative PDF-object removal for verified record-site watermark layouts.

Scan streams are never decoded/re-encoded by the writer. Unknown layouts are
retained. Form edits are copy-on-write so shared resources on unchecked pages
cannot be changed by a selected page.
"""
import hashlib
import json
import math
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, DictionaryObject, NameObject, DecodedStreamObject

COOK_TEXT = {'UNOFFICIAL COPY', "Property of Cook County Clerk's Office"}
# Fingerprint of the complete vector overlay resource tree, excluding compression.
OKCOUNTY_SIGNATURES = {'718b3e476d6fb4749ee58c0538191fc01cc5cb4a77831ff86d0ab21841277546'}
HARRISON_MAP_CONTENT = 'd3160b3cb9467738b9f1b19f438d1a8cf39431207149a06f296c37cbc0653d8c'
HARRISON_MAP_IMAGES = {
    '8daa2ad4086f56e16b682d7f1a9999ee0a67b143450009375167c2f0c5f20f19',
    '8e57fbcf158db575f0c0ced20e31ecc122c00daded1f8d49abc14857ef27ab56',
}


def resolved(value):
    return value.get_object() if hasattr(value, 'get_object') else value


def read_pdf(path):
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('This PDF needs a password. Save an unlocked copy first.')
    return reader


def form_signature(obj):
    budget = [20000]
    def canonical(value, depth=0):
        budget[0] -= 1
        if depth > 20 or budget[0] < 0:
            raise ValueError('Overlay resource tree is too complex.')
        value = value.get_object() if hasattr(value, 'get_object') else value
        if isinstance(value, dict):
            result = {str(k): canonical(v, depth+1) for k, v in sorted(value.items())
                      if k not in ('/Length', '/Filter', '/DecodeParms')}
            if hasattr(value, 'get_data'):
                result['stream_sha256'] = hashlib.sha256(value.get_data()).hexdigest()
            return result
        if isinstance(value, (list, tuple)):
            return [canonical(v, depth+1) for v in value]
        return str(value)
    return hashlib.sha256(json.dumps(canonical(obj), sort_keys=True).encode()).hexdigest()


def cook_ranges(ops, resources):
    """Only standalone, graphics-state-isolated text blocks beside a scan."""
    resources = resolved(resources)
    xobjects = resolved(resources.get('/XObject', {}))
    if not any(op == b'Do' and args[0] in xobjects and
               xobjects[args[0]].get_object().get('/Subtype') == '/Image' for args, op in ops):
        return []
    ranges = []; depth = 0; start = None
    permitted = {b'q', b'Q', b'cm', b'BT', b'ET', b'Tm', b'Tf', b'rg', b'g', b'Tj'}
    for i, (args, op) in enumerate(ops):
        if op == b'q':
            if depth == 0: start = i
            depth += 1
        elif op == b'Q':
            depth -= 1
            if depth < 0: return []
            if depth == 0 and start is not None:
                block = ops[start:i+1]
                texts = [str(a[0]) for a, o in block if o == b'Tj']
                fonts = [a[0] for a, o in block if o == b'Tf']
                if (len(texts) == 1 and texts[0] in COOK_TEXT and len(fonts) == 1
                        and all(o in permitted for a, o in block)
                        and sum(o == b'BT' for a, o in block) == 1
                        and sum(o == b'ET' for a, o in block) == 1):
                    font = resolved(resources.get('/Font', {})).get(fonts[0])
                    font = font.get_object() if font else {}
                    if font.get('/BaseFont') == '/Helvetica' and font.get('/Subtype') == '/Type1' and not font.get('/ToUnicode') and font.get('/Encoding') in (None, '/WinAnsiEncoding'):
                        ranges.append((start, i+1, texts[0]))
    if depth or {r[2] for r in ranges} != COOK_TEXT:
        return []
    return ranges


def apdf_ranges(ops, resources):
    """Recognize the two verified A-PDF text layouts, not arbitrary diagonal text.

    Require an isolated, balanced block, standard Helvetica mapping, the A-PDF
    resource names, and its complete drawing grammar. Extra text, paths, custom
    encodings or graphics-state effects make the entire block ineligible.
    """
    resources = resolved(resources)
    fonts = resolved(resources.get('/Font', {}))
    states = resolved(resources.get('/ExtGState', {}))
    def close(values, expected):
        return len(values) == len(expected) and all(
            math.isfinite(float(a)) and abs(float(a)-b) < 0.00001
            for a, b in zip(values, expected))
    def identify(block):
        prefix = [b'q', b'gs', b'cm', b'Tr', b'Tf', b'rg', b'Tz', b'Ts', b'Tw', b'Tc']
        if [o for _, o in block[:10]] != prefix or block[-1] != ([], b'Q'):
            return None
        gs = block[1][0]; font = block[4][0]
        if len(gs) != 1 or len(font) != 2:
            return None
        if not str(gs[0]).startswith('/QQAPGS') or not str(font[0]).startswith('/QQAPF'):
            return None
        font_object=resolved(fonts.get(font[0], {}))
        families={'/Helvetica','/Helvetica-Bold','/Helvetica-Oblique','/Helvetica-BoldOblique',
                  '/Times-Roman','/Times-Bold','/Times-Italic','/Times-BoldItalic',
                  '/Courier','/Courier-Bold','/Courier-Oblique','/Courier-BoldOblique'}
        if (set(font_object)!={'/Type','/Subtype','/BaseFont','/Encoding'} or
                font_object.get('/BaseFont') not in families or font_object.get('/Type')!='/Font' or
                font_object.get('/Subtype')!='/Type1' or font_object.get('/Encoding')!='/WinAnsiEncoding'):
            return None
        state = resolved(states.get(gs[0], {}))
        if set(state) != {'/Type', '/SMask', '/ca', '/CA'} or state['/Type'] != '/ExtGState' or state['/SMask'] != '/None':
            return None
        for index, expected in [(3, [0]), (6, [100]), (7, [0]), (8, [0]), (9, [0])]:
            if not close(block[index][0], expected):
                return None
        profiles=[
                ('www.apdf.com', 30, [.8196, .4941, .6235], .44, 21, 'A-PDF tiled text'),
                ('Sample', 103.1638, [0, 0, 1], .51, 45, 'A-PDF rotated Sample')]
        texts=[str(a[0]) for a,o in block if o==b'Tj']
        matrix=block[2][0];color=block[5][0];alpha=float(state['/ca'])
        if (texts and len(set(texts))==1 and 1<=len(texts[0])<=100 and
                (len(texts)>=2 or texts[0].strip().upper() in {'SAMPLE','DRAFT','COPY','CONFIDENTIAL'}) and
                18<=float(font[1])<=240 and len(color)==3 and all(0<=float(c)<=1 for c in color) and
                .05<=alpha<=.85 and len(matrix)==6):
            angle=math.degrees(math.atan2(float(matrix[1]),float(matrix[0])))
            if 5<=abs(angle)<=85:
                profiles.append((texts[0],float(font[1]),[float(c) for c in color],alpha,angle,'A-PDF text variant'))
        for text, size, color, alpha, angle, label in profiles:
            if not (close([font[1]], [size]) and close(block[5][0], color)
                    and close([state['/ca'], state['/CA']], [alpha, alpha])):
                continue
            cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            cursor = 10; matrix = block[2][0]; count = 0
            while cursor < len(block)-1:
                segment = block[cursor:cursor+6]
                if [o for _, o in segment] != [b'BT', b'Tm', b'Tj', b'ET', b'cm', b'cm']:
                    return None
                if (len(matrix) != 6 or not close(matrix[:4], [cosine, sine, -sine, cosine])
                        or not close(segment[1][0], [1, 0, 0, 1, 0, 0])
                        or segment[2][0] != [text]
                        or not close(segment[4][0], [cosine, -sine, sine, cosine, 0, 0])
                        or not close(segment[5][0], [1, 0, 0, 1, -float(matrix[4]), -float(matrix[5])])):
                    return None
                count += 1; cursor += 6
                if cursor < len(block)-1:
                    if block[cursor][1] != b'cm': return None
                    matrix = block[cursor][0]; cursor += 1
            if cursor == len(block)-1 and (count>=2 or (count==1 and text.strip().upper() in {'SAMPLE','DRAFT','COPY','CONFIDENTIAL'})):
                return label
        return None
    ranges = []; level = 0; start = 0
    for i, (_, op) in enumerate(ops):
        if op == b'q':
            if level == 0: start = i
            level += 1
        elif op == b'Q':
            level -= 1
            if level < 0: return []
            if level == 0:
                try:
                    label = identify(ops[start:i+1])
                except (TypeError, ValueError, KeyError, IndexError, OverflowError):
                    label = None
                if label: ranges.append((start, i+1, label))
    return [] if level else ranges


def unofficial_text_ranges(ops, resources):
    """Verified Harrison scan/map suffix: remove the overlay, never scan pixels.

    CID strings are glyph IDs, not readable text. Check the actual Unicode map
    as well as the glyph bytes, font, angle, opacity and complete suffix grammar.
    Only a terminal block is eligible, so deleting its state cannot affect later
    document content. Unknown encodings and additional drawing stay untouched.
    """
    if len(ops) < 9:
        return []
    block = ops[-8:]
    if [op for _, op in block] != [b'gs', b'BT', b'rg', b'Tf', b'Tm', b'Tj', b'Tm', b'ET']:
        return []
    try:
        resources = resolved(resources)
        objects = resolved(resources.get('/XObject', {}))
        prefix = ops[:-8]
        level = 0
        for args, op in prefix:
            if op in (b'Tj', b'TJ', b"'", b'"'):
                return []
            if op == b'q': level += 1
            if op == b'Q': level -= 1
            if level < 0: return []
        if level or not any(op == b'Do' and len(args) == 1 and
                            resolved(objects.get(args[0], {})).get('/Subtype') == '/Image'
                            for args, op in prefix):
            return []
        def close(values, expected):
            return len(values) == len(expected) and all(
                math.isfinite(float(a)) and abs(float(a)-b) < .00001
                for a, b in zip(values, expected))
        if len(block[0][0]) != 1 or len(block[3][0]) != 2 or len(block[5][0]) != 1:
            return []
        state = resolved(resolved(resources.get('/ExtGState', {})).get(block[0][0][0], {}))
        font = resolved(resolved(resources.get('/Font', {})).get(block[3][0][0], {}))
        cmap = resolved(font.get('/ToUnicode'))
        if (set(state) != {'/ca'} or not close([state['/ca']], [.70588])
                or font.get('/Subtype') != '/Type0' or font.get('/Encoding') != '/Identity-H'
                or str(font.get('/BaseFont', '')).split('+')[-1] != 'OpenSans-Bold'
                or not hasattr(cmap, 'get_data')
                or hashlib.sha256(cmap.get_data()).hexdigest() !=
                '49257e90a1a3a1cbba47de4cd7295f20ca0c311dba4037e11faf73cf92b368cd'):
            return []
        text = block[5][0][0]
        raw = text.original_bytes if hasattr(text, 'original_bytes') else bytes(text)
        matrix = block[4][0]
        if (raw != bytes.fromhex('003800510010003200490049004c0046004c0044004f')
                or not close(block[2][0], [.75294]*3)
                or not 24 <= float(block[3][0][1]) <= 2000
                or len(matrix) != 6 or not all(math.isfinite(float(v)) for v in matrix)
                or not close(matrix[:4], [.70711, -.70711, .70711, .70711])
                or not close(block[6][0], [1, 0, 0, 1, 0, 0])):
            return []
        return [(len(ops)-8, len(ops), 'Harrison County Un-Official text')]
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError):
        return []


def lettered_unofficial_ranges(ops, resources):
    """Recorder overlay with ten individually positioned, translucent glyphs."""
    word = 'UNOFFICIAL'
    start = len(ops) - (3 + 5*len(word))
    if start < 1 or [op for _,op in ops[start:start+2]] != [b'rg', b'gs'] or ops[-1][1] != b'Q':
        return []
    try:
        rgb, state_name = ops[start][0], ops[start+1][0][0]
        state = resolved(resolved(resources['/ExtGState'])[state_name])
        if len(rgb)!=3 or any(abs(float(v)-.25098)>.0001 for v in rgb) or abs(float(state['/ca'])-.35)>.001:
            return []
        fonts = resolved(resources['/Font'])
        objects = resolved(resources['/XObject'])
        if not any(op==b'Do' and resolved(objects.get(args[0],{})).get('/Subtype')=='/Image'
                   for args,op in ops[:start]):
            return []
        last = None
        for j,letter in enumerate(word):
            offset = start+2+j*5
            block = ops[offset:offset+5]
            if [op for _,op in block] != [b'BT', b'Tm', b'Tf', b'Tj', b'ET']:
                return []
            a,b,c,d,x,y = map(float,block[1][0])
            font_name,size = block[2][0]
            font = resolved(fonts[font_name])
            if (str(block[3][0][0])!=letter or size!=1 or font.get('/Subtype')!='/TrueType'
                    or not str(font.get('/BaseFont','')).endswith('Calibri-Bold')
                    or not (70<a<110 and -150<b<-110 and abs(c-b)<2 and abs(d+a)<2)
                    or (last and not (x>last[0] and y<last[1] and last[2]==font_name))):
                return []
            last = x,y,font_name
        return [(start+2,len(ops)-1,'Diagonal UNOFFICIAL letters')]
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return []


def harrison_map_overlay(stream, xobjects):
    # The top image contains map content as well as the stamp. Remove it only
    # when the drawing instructions and all underlying scans are verified.
    names = ('/Image1', '/Image2', '/Image3')
    if set(xobjects) != set(names):
        return False
    if hashlib.sha256(stream.get_data()).hexdigest() != HARRISON_MAP_CONTENT:
        return False
    try:
        signatures = ''.join(form_signature(xobjects[name]) for name in names)
    except (ValueError, RecursionError):
        return False
    return hashlib.sha256(signatures.encode()).hexdigest() in HARRISON_MAP_IMAGES


def rewrite_node(node, resources, owner, writer=None, depth=0, ancestors=()):
    if depth > 16 or id(node) in ancestors:
        return None, resources, []
    resources = resolved(resources)
    stream = ContentStream(node, owner)
    xobjects = resolved(resources.get('/XObject', {}))
    map_overlay = depth == 0 and harrison_map_overlay(stream, xobjects)
    ops = stream.operations
    cook = cook_ranges(ops, resources)
    apdf = apdf_ranges(ops, resources)
    unofficial = unofficial_text_ranges(ops, resources) + lettered_unofficial_ranges(ops, resources)
    ranges = cook + apdf + unofficial
    remove = {i for start, end, _ in ranges for i in range(start, end)}
    labels = ['Cook County'] * len(cook) + [label for _, _, label in apdf + unofficial]
    updated = []; removed_names = set()
    new_resources = DictionaryObject(dict(resources))
    new_xobjects = DictionaryObject(dict(xobjects))
    for i, (args, op) in enumerate(ops):
        if i in remove:
            continue
        if op == b'Do' and args[0] in xobjects:
            if map_overlay and args[0] == '/Image3':
                labels.append('Harrison County map')
                removed_names.add(args[0])
                continue
            child = xobjects[args[0]].get_object()
            if child.get('/Subtype') == '/Form':
                try:
                    known = form_signature(child) in OKCOUNTY_SIGNATURES
                except (ValueError, RecursionError):
                    known = False
                if known:
                    labels.append('OKCountyRecords')
                    removed_names.add(args[0])
                    continue
                child_resources = child.get('/Resources', resources)
                content, res, found = rewrite_node(child, child_resources, owner, writer,
                                                   depth+1, (*ancestors, id(node)))
                if found:
                    labels.extend(found)
                    if writer is not None:
                        replacement = DecodedStreamObject()
                        replacement.update({k: v for k, v in child.items()
                                            if k not in ('/Length', '/Filter', '/DecodeParms')})
                        replacement.set_data(content)
                        replacement[NameObject('/Resources')] = res
                        # A fresh name also protects a repeated call within this stream.
                        name = NameObject('/ClearPageOverlay' + str(i))
                        while name in new_xobjects:
                            name = NameObject(str(name) + '_')
                        new_xobjects[name] = writer._add_object(replacement)
                        removed_names.add(args[0])
                        args = [name]
        updated.append((args, op))
    if not labels:
        return None, resources, []
    if writer is None:
        return None, resources, labels
    used_names = {a[0] for a, o in updated if o == b'Do'}
    for name in removed_names - used_names:
        new_xobjects.pop(name, None)
    new_resources[NameObject('/XObject')] = new_xobjects
    stream = ContentStream(None, owner)
    stream.operations = updated
    return stream.get_data(), new_resources, labels


def inspect(path):
    reader = read_pdf(path)
    pages = []
    for page in reader.pages:
        _, _, labels = rewrite_node(page.get_contents(), page.get('/Resources', {}), reader)
        pages.append(dict(overlays=len(labels), providers=sorted(set(labels))))
    return pages


def write_clean(source, target, keep, cancelled=lambda: False):
    reader = read_pdf(source)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    reports = []
    for index, page in enumerate(writer.pages):
        if cancelled(): raise InterruptedError('Cancelled')
        labels = []
        if index in keep:
            content, resources, labels = rewrite_node(page.get_contents(), page.get('/Resources', {}), writer, writer)
            if labels:
                stream = DecodedStreamObject(); stream.set_data(content)
                page[NameObject('/Contents')] = writer._add_object(stream)
                page[NameObject('/Resources')] = resources
        reports.append(dict(overlays=len(labels), providers=sorted(set(labels))))
    if cancelled(): raise InterruptedError('Cancelled')
    temporary = Path(str(target) + '.part')
    try:
        writer.write(temporary)
        if cancelled(): raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return reports
