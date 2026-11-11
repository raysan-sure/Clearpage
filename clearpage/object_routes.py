"""Local candidate selection with preview-first export and reversible choices."""
from flask import request,jsonify,send_file
from pypdf import PdfWriter
import numpy as np
from clearpage.pdf import pdf_selection


def register(a):
    def clear_result(job):
        job.update(state='ready',results=[],options=a.settings({'method':'object'}))
        job['ui']=dict(page=job.get('ui',{}).get('page',0),keep=[],reviewed=[],confirmed=False,options=job['options'])
        for name in ('selected-preview.pdf','cleaned.pdf'):(job['dir']/name).unlink(missing_ok=True)
        a.require_new_review(job)

    @a.app.post('/api/objects/<identifier>/<int:index>')
    def candidates(identifier,index):
        job=a.job_for(identifier);a.original(job,index)
        reader=pdf_selection.read_pdf(job['source'])
        items=pdf_selection.inspect_page(reader,index)
        if len(items)>24:raise ValueError('More than 24 text candidates on this page. Use the painted-area tool for this document.')
        original=np.array(a.render_page(job['source'],index));height,width=original.shape[:2]
        # Candidate previews need only this page, even for a 300-page input.
        single=job['dir']/'candidate-source.pdf'
        writer=PdfWriter();writer.add_page(reader.pages[index]);writer.write(single)
        visible=[]
        for candidate in items:
            target=job['dir']/'candidate-preview.pdf'
            pdf_selection.write_selected(single,target,[0],{'0':[candidate['id']]})
            changed=np.any(np.array(a.render_page(target,0))!=original,axis=2)
            yy,xx=np.where(changed)
            if len(xx):
                visible.append(dict(id=candidate['id'],text=candidate['text'],
                    box=[float(xx.min()/width),float(yy.min()/height),float((xx.max()+1)/width),float((yy.max()+1)/height)]))
        job['object_candidates']={'page':index,'items':visible}
        return jsonify(items=visible,note='Click the watermark on Before or choose its text below. Only isolated standard-font text blocks are offered; scanned marks need image cleanup.')

    @a.app.post('/api/objects/<identifier>/select')
    def select(identifier):
        job=a.job_for(identifier);data=request.get_json();index=data.get('page');identity=data.get('id')
        if type(index)is not int or not 0<=index<job['count']:raise ValueError('Invalid page.')
        reader=pdf_selection.read_pdf(job['source']);items=pdf_selection.inspect_page(reader,index)
        chosen=next((c for c in items if c['id']==identity),None)
        if chosen is None:raise ValueError('Find the text objects again before selecting one.')
        # One atomic replacement; matching pages only share the exact appearance key.
        selection={str(index):[identity]}
        if data.get('all_pages'):
            selection={str(i):[c['id'] for c in pdf_selection.inspect_page(reader,i) if c['signature']==chosen['signature']]
                       for i in range(job['count'])}
            selection={k:v for k,v in selection.items() if v}
        job['object_selection']=selection
        clear_result(job)
        job['ui']['page']=index
        return jsonify(job=a.public(job),pages=sorted(int(k)+1 for k in selection),text=chosen['text'])

    @a.app.post('/api/objects/<identifier>/clear')
    def clear_object_selection(identifier):
        job=a.job_for(identifier);job.pop('object_selection',None);clear_result(job)
        return jsonify(a.public(job))
