"""Non-AI repeated-lettering preservation and the actual image upload route."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import app
from clearpage.pdf import engine
from clearpage.presets import scan_patterns
from tests.test_scan_patterns import repeated_fixture


def document_fixture():
    layer=Image.new('L',(3000,3000),255);mark=ImageDraw.Draw(layer)
    mark_font=ImageFont.load_default(size=17)
    for y in range(50,3000,180):
        mark.text((0,y),('Printing or saving this document is restricted. Report duplication. '*8),font=mark_font,fill=150)
    layer=layer.rotate(45,expand=False,fillcolor=255).crop((900,600,2100,2400))
    watermark=np.repeat(np.array(layer)[:,:,None],3,axis=2)
    page=Image.new('RGB',(1200,1800),'white');draw=ImageDraw.Draw(page)
    font=ImageFont.load_default(size=18)
    draw.text((520,70),'2612400',font=font,fill=(0,0,0))
    for i,y in enumerate(range(400,950,38)):
        ink=195 if i%3==0 else 15
        draw.text((150,y),'Recorded parcel 34, block 4; 07/29/2026. Original words and numbers 123456789.',font=font,fill=(ink,ink,ink))
    draw.line([(160,1050),(185,1000),(200,1060),(260,1010),(300,1055),(390,1030)],fill=(35,35,35),width=3)
    draw.line((150,1065,500,1065),fill=(0,0,0),width=2)
    draw.rectangle((650,1000,980,1100),outline=(20,20,20),width=3)
    draw.text((670,1020),'NOTARY - ORIGINAL 12345',font=font,fill=(10,10,10))
    for x in range(200,950,5):draw.rectangle((x,1300,x+2,1350),fill=(0,0,0))
    source=np.array(page)
    return np.minimum(source,watermark),source


class RepeatedDocumentTests(unittest.TestCase):
    def test_rows_short_numbers_faint_text_signatures_and_barcodes_preserved(self):
        original,source=document_fixture()
        for factor in (0.75,1.,1.5):
            size=tuple(round(v*factor) for v in (1200,1800))
            a=np.array(Image.fromarray(original).resize(size,Image.Resampling.NEAREST))
            body=np.array(Image.fromarray(source).resize(size,Image.Resampling.NEAREST))
            out,changed,result=scan_patterns.repeated_document(Image.fromarray(a))
            self.assertTrue(result['matched'],(factor,result))
            self.assertTrue(result['partial']);self.assertTrue(result['uncertain'])
            self.assertGreater(result['line_guides'],3)
            np.testing.assert_array_equal(out[body.min(2)<255],a[body.min(2)<255])
            band=scan_patterns._repeated_detection(Image.fromarray(a))[4]
            self.assertFalse(changed[~band].any())
            np.testing.assert_array_equal(changed,np.any(a!=out,2))
            # Better removal than the margins-only method at the base size.
            if factor==1:
                self.assertTrue(result['template_matched'])
                self.assertGreater(result['template_extra_pixels'],100)
                old=scan_patterns.repeated_diagonal(Image.fromarray(a))[2]
                self.assertGreater(result['changed'],old['changed'])

    def test_clean_page_and_ambiguous_no_rows_are_unchanged(self):
        _,body=document_fixture()
        no_rows=repeated_fixture()
        for y in (500,700,900):no_rows[y:y+6,300:900]=255
        for a in (body,no_rows,np.full((600,400,3),255,'uint8')):
            out,changed,result=scan_patterns.repeated_document(Image.fromarray(a))
            self.assertFalse(changed.any());self.assertFalse(result['matched'])
            np.testing.assert_array_equal(out,a)
        with self.assertRaises(InterruptedError):
            scan_patterns.repeated_document(Image.fromarray(body),lambda:True)

    def test_image_upload_preview_process_and_selective_export_without_ai(self):
        a,_=document_fixture();image=Image.fromarray(a)
        data=BytesIO();image.save(data,format='PNG')
        client=app.app.test_client();headers={'X-ClearPage':'local','X-Filename':'example.png'}
        response=client.post('/api/upload',data=data.getvalue(),headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        job=app.JOBS[response.json['id']]
        try:
            with patch.object(app.neural_restore,'inpaint',side_effect=AssertionError('AI called')):
                options=app.settings({'method':'repeated_document','mode':'lama','auto_restore':True})
                self.assertEqual(options['mode'],'careful');self.assertFalse(options['auto_restore'])
                response=client.post('/api/preview/'+job['id'],json=options,headers=headers)
                self.assertEqual(response.status_code,200,response.json)
                preview=np.array(Image.open(app.image_path(job,0,True)))
                job.update(options=options,results=[],cancel=False);app.batch(job,options)
                self.assertEqual(job['state'],'review',job['message'])
                self.assertTrue(job['results'][0]['partial']);self.assertTrue(job['results'][0]['uncertain'])
                np.testing.assert_array_equal(preview,Image.open(app.image_path(job,0,True)))
                for keep in ([],[0]):
                    app.export_worker(job,keep)
                    self.assertEqual(job['state'],'done',job['message'])
                    expected=preview if keep else np.array(engine.render_page(job['source'],0))
                    np.testing.assert_array_equal(engine.render_page(job['dir']/'cleaned.pdf',0),expected)
        finally:
            job['state']='ready';client.post('/api/clear/'+job['id'],json={},headers=headers)


if __name__=='__main__':unittest.main()
