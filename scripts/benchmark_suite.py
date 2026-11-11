"""Deterministic synthetic corpus. Scores are not company-document accuracy."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image,ImageDraw
from pypdf import PdfWriter
from pypdf.generic import ArrayObject,NameObject,FloatObject
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from clearpage.pdf import pdf_overlays, pdf_selection, engine, page_polarity
import benchmark
from tests.test_apdf import sample


def run(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);cases=[]
    for name,tiled,mutation,manual in [
        ('apdf-tiled',True,None,False),('apdf-rotated',False,None,False),
        ('apdf-variant',False,lambda m,f,s:m.replace(b'(Sample)',b'(DRAFT)').replace(b'103.1638 Tf',b'45 Tf').replace(b'0 0 1 rg',b'.1 .3 .2 rg'),False),
        ('selected-text',False,lambda m,f,s:m.replace(b'(Sample)',b'(PRIVATE MARK)'),True)]:
        source=root/(name+'-source.pdf');result=root/(name+'-result.pdf');reference=root/(name+'-reference.pdf')
        sample(source,tiled,mutation)
        # Ground truth contains only the independently generated body stream.
        writer=PdfWriter();writer.clone_document_from_reader(pdf_overlays.read_pdf(source))
        for page in writer.pages:page[NameObject('/Contents')]=ArrayObject([page['/Contents'][1]])
        writer.write(reference)
        if manual:
            reader=pdf_selection.read_pdf(source)
            selection={str(i):[c['id'] for c in pdf_selection.inspect_page(reader,i) if c['text']=='PRIVATE MARK'] for i in range(2)}
            pdf_selection.write_selected(source,result,[0,1],selection)
        else:pdf_overlays.write_clean(source,result,[0,1])
        cases.append(dict(id=name,category='selected-objects' if manual else 'automatic-objects',source=source.name,result=result.name,reference=reference.name))
    def image_pdf(image,target):
        c=Canvas(str(target),pagesize=(300,400));c.drawImage(ImageReader(image),0,0,300,400);c.showPage();c.save()
    positive=Image.new('RGB',(600,800),'white');draw=ImageDraw.Draw(positive)
    draw.text((50,50),'Known synthetic wording 12345',fill='black')
    draw.line((30,300,570,300),fill=(210,210,210),width=3)
    for name in ('normal-scan','negative-scan','faint-text-overlap'):
        source=root/(name+'-source.pdf');result=root/(name+'-result.pdf');reference=root/(name+'-reference.pdf')
        pixels=np.array(positive)
        if name=='negative-scan':pixels=255-pixels
        elif name=='faint-text-overlap':
            # Add a pale mark without replacing the known faint stroke.
            pixels[270:330,280:320]=np.minimum(pixels[270:330,280:320],235)
        image_pdf(Image.fromarray(pixels),source);image_pdf(positive,reference)
        if name=='faint-text-overlap':
            mask=np.zeros((800,600),bool);mask[270:330,280:320]=True
            cleaned,_=engine.clean(Image.fromarray(pixels),mask,'careful')
        else:cleaned,_=page_polarity.correct(Image.fromarray(pixels),name=='negative-scan')
        image_pdf(Image.fromarray(cleaned),result)
        cases.append(dict(id=name,category=name,source=source.name,result=result.name,reference=reference.name))
    manifest=root/'manifest.json';manifest.write_text(json.dumps({'cases':cases},indent=2))
    report=benchmark.run(manifest)
    # Fail on damaged native content or unmarked scan pixels; overlap residue
    # is reported, never mistaken for successful removal.
    failures=[]
    for row in report['pages']:
        if row['category'] in ('automatic-objects','selected-objects','normal-scan','negative-scan') and row['mean_rgb_error']!=0:
            failures.append(f"{row['case']} page {row['page']}: expected exact reference")
        if row['preserved_reference_matching_pixels_percent'] not in (None,100):failures.append(f"{row['case']}: changed reference-matching pixels")
        if row['faint_ink_pixel_match_percent'] not in (None,100):failures.append(f"{row['case']}: faint ink damaged")
    report['gate_failures']=failures
    (root/'benchmark-report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    result=run(parser.parse_args().output_dir)
    print(json.dumps({'pages':len(result['pages']),'categories':len(result['categories']),'failures':result['gate_failures']}))
    if result['gate_failures']:raise SystemExit(1)
