"""User-selected isolated text blocks. Never guess or repaint page content."""
import hashlib
import json
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import ContentStream, DictionaryObject, DecodedStreamObject, NameObject
from clearpage.pdf.pdf_overlays import read_pdf, resolved, form_signature

TEXT_OPS = {b'q',b'Q',b'BT',b'ET',b'Tf',b'Tm',b'Tj',b'TJ',b'Td',b'TD',b'T*',
            b'Tr',b'Ts',b'Tz',b'Tc',b'Tw',b'rg',b'g',b'RG',b'G',b'cm',b'gs'}


def blocks(ops, resources):
    """Only self-contained repeated text, not mixed paragraphs or images."""
    # Text matrices are not saved by q/Q. Refuse streams that depend on a
    # previous text object's position, so removing one cannot move later text.
    positioned=False
    for args,op in ops:
        if op==b'BT':positioned=False
        elif op==b'Tm':positioned=True
        elif op in (b'Tj',b'TJ') and not positioned:return []
    found=[]; depth=0; start=0
    for end,(args,op) in enumerate(ops):
        if op==b'q':
            if depth==0:start=end
            depth+=1
        elif op==b'Q':
            depth-=1
            if depth<0:return []
            if depth:continue
            part=ops[start:end+1]
            if not all(o in TEXT_OPS for a,o in part):continue
            if any(o==b'Tr' and a!=[0] for a,o in part):continue
            texts=[]
            for a,o in part:
                if o==b'Tj':texts.append(str(a[0]))
                if o==b'TJ':texts.append(''.join(x for x in a[0] if isinstance(x,str)))
            if not texts or len(texts)>100 or len(set(texts))!=1 or not 1<=len(texts[0].strip())<=100:continue
            fs=[a for a,o in part if o==b'Tf']
            if len(fs)!=1 or len(fs[0])!=2 or not 12<=float(fs[0][1])<=300:continue
            fonts=resolved(resources.get('/Font',{}));font=resolved(fonts.get(fs[0][0],{}))
            # Do not guess text from arbitrary embedded/custom encodings.
            if font.get('/Subtype')!='/Type1' or font.get('/ToUnicode') or font.get('/Encoding') not in (None,'/WinAnsiEncoding'):continue
            if font.get('/BaseFont') not in {'/Helvetica','/Helvetica-Bold','/Helvetica-Oblique','/Helvetica-BoldOblique',
                    '/Times-Roman','/Times-Bold','/Times-Italic','/Times-BoldItalic','/Courier','/Courier-Bold','/Courier-Oblique','/Courier-BoldOblique'}:continue
            if sum(o==b'BT' for a,o in part)!=len(texts) or sum(o==b'ET' for a,o in part)!=len(texts):continue
            # Appearance key ignores placement and resource names, not fonts or effects.
            appearance=[]
            for a,o in part:
                if o==b'Tf':a=[form_signature(font),a[1]]
                elif o==b'gs':
                    state=resolved(resolved(resources.get('/ExtGState',{})).get(a[0],{}))
                    if not state or state.get('/SMask','/None')!='/None':break
                    a=[form_signature(state)]
                elif o in (b'cm',b'Tm'):a=a[:4]
                elif o in (b'Td',b'TD'):continue
                appearance.append((str(a),o.decode()))
            else:
                signature=hashlib.sha256(json.dumps(appearance).encode()).hexdigest()
                found.append(dict(start=start,end=end+1,text=texts[0],signature=signature))
    return [] if depth else found


def walk(node, resources, owner, path=(), ancestors=(), depth=0):
    if depth>12 or id(node) in ancestors:raise ValueError('PDF form nesting is too complex for text selection.')
    resources=resolved(resources);ops=ContentStream(node,owner).operations
    if len(ops)>50000:raise ValueError('This page has too many drawing operations for text selection.')
    found=[]
    for block in blocks(ops,resources):
        identity=hashlib.sha256(json.dumps([path,block['start'],block['end'],block['signature']]).encode()).hexdigest()
        found.append(dict(block,id=identity,path=list(path)))
    xobjects=resolved(resources.get('/XObject',{}))
    for i,(a,o) in enumerate(ops):
        if o==b'Do' and a[0] in xobjects:
            child=resolved(xobjects[a[0]])
            if child.get('/Subtype')=='/Form':
                found.extend(walk(child,child.get('/Resources',resources),owner,(*path,i),(*ancestors,id(node)),depth+1))
        if len(found)>64:raise ValueError('Too many separate text candidates on this page. Use a smaller PDF or the painted-area tool.')
    return found


def inspect_page(reader,index):
    p=reader.pages[index]
    return walk(p.get_contents(),p.get('/Resources',{}),reader)


def rewrite(node,resources,writer,selected,path=()):
    ops=ContentStream(node,writer).operations;resources=resolved(resources)
    remove={i for b in selected if b['path']==list(path) for i in range(b['start'],b['end'])}
    newres=DictionaryObject(dict(resources));xs=resolved(resources.get('/XObject',{}));newxs=DictionaryObject(dict(xs))
    updated=[]
    for i,(args,op) in enumerate(ops):
        if i in remove:continue
        childpath=[*path,i]
        if op==b'Do' and any(b['path'][:len(childpath)]==childpath for b in selected):
            child=resolved(xs[args[0]])
            data,res=rewrite(child,child.get('/Resources',resources),writer,selected,tuple(childpath))
            replacement=DecodedStreamObject();replacement.update({k:v for k,v in child.items() if k not in ('/Length','/Filter','/DecodeParms')})
            replacement.set_data(data);replacement[NameObject('/Resources')]=res
            name=NameObject('/ClearPageSelected'+str(i))
            while name in newxs:name=NameObject(str(name)+'_')
            newxs[name]=writer._add_object(replacement);args=[name]
        updated.append((args,op))
    newres[NameObject('/XObject')]=newxs
    stream=ContentStream(None,writer);stream.operations=updated
    return stream.get_data(),newres


def write_selected(source,target,keep,selection,cancelled=lambda:False):
    writer=PdfWriter();writer.clone_document_from_reader(read_pdf(source))
    for index,p in enumerate(writer.pages):
        if cancelled():raise InterruptedError('Cancelled')
        ids=set(selection.get(str(index),[])) if index in keep else set()
        if not ids:continue
        candidates=inspect_page(writer,index);chosen=[c for c in candidates if c['id'] in ids]
        if len(chosen)!=len(ids):raise ValueError('Text selection is stale. Find and select the watermark again.')
        data,res=rewrite(p.get_contents(),p.get('/Resources',{}),writer,chosen)
        stream=DecodedStreamObject();stream.set_data(data)
        p[NameObject('/Contents')]=writer._add_object(stream);p[NameObject('/Resources')]=res
    temporary=Path(str(target)+'.part')
    try:
        writer.write(temporary)
        if cancelled():raise InterruptedError('Cancelled')
        temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
