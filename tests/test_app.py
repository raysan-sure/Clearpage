"""Run with python -m unittest tests.test_app; synthetic fixtures only, no company documents."""
from pathlib import Path
import tempfile
import time
import unittest
from io import BytesIO
import numpy as np
from PIL import Image, ImageDraw
from PIL import ImageFont
from unittest.mock import patch
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter
from clearpage.pdf import engine
import app as application
from clearpage.restore import word_repair
from clearpage.restore import auto_restore
from clearpage.restore import scan_inversion
from clearpage.restore import neural_restore
from clearpage.restore import text_restore
from vendor.purescan_core import extract_template, invert_multiply


def fixture(path):
    image = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((40, 60), 'ORIGINAL DOCUMENT 12345', fill='black')
    draw.rectangle((200, 300, 400, 330), fill=(170, 170, 170))
    draw.line((30, 120, 550, 120), fill='black', width=2)
    memory = BytesIO()
    canvas = Canvas(memory, pagesize=(300, 400))
    for _ in range(4):
        canvas.drawImage(ImageReader(image), 0, 0, width=300, height=400)
        canvas.showPage()
    canvas.save()
    reader = PdfReader(memory)
    reader.pages[1].rotate(90)
    reader.pages[2].cropbox.lower_left = (20, 30)
    reader.pages[2].cropbox.upper_right = (280, 380)
    reader.pages[3].rotate(270)
    reader.pages[3].cropbox.lower_left = (20, 30)
    reader.pages[3].cropbox.upper_right = (280, 380)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    with open(path, 'wb') as output:
        writer.write(output)


class EngineTests(unittest.TestCase):
    def test_font_fit_recovers_known_strokes_and_refuses_no_evidence(self):
        image=Image.new('L',(180,45),255)
        ImageDraw.Draw(image).text((6,5),'degrees',font=ImageFont.truetype('C:/Windows/Fonts/times.ttf',25),fill=20)
        truth=np.array(image);ys,xs=np.where(truth<250)
        box=[int(xs.min())-2,int(ys.min())-2,int(xs.max())+3,int(ys.max())+3]
        mask=np.zeros(truth.shape,bool);mask[:,46:51]=True
        damaged=truth.copy();damaged[mask]=0
        result=text_restore.fit(damaged,mask,box,'degrees')
        self.assertIsNotNone(result);self.assertGreater(result['score'],.93)
        x,y,xx,yy=box;cut=truth[y:yy,x:xx];selected=mask[y:yy,x:xx]
        self.assertLess(np.abs(result['gray'].astype(float)-cut)[selected].mean(),50)
        self.assertIsNone(text_restore.fit(damaged,np.ones_like(mask),box,'degrees'))
        with self.assertRaises(InterruptedError):text_restore.fit(damaged,mask,box,'degrees',lambda:True)

    def test_text_recognizer_integrity_and_input_limits(self):
        bad=self.root/'bad.onnx';bad.write_bytes(b'bad model')
        with self.assertRaises(ValueError):text_restore.load_recognizer(bad)
        with self.assertRaises(ValueError):text_restore.recognize(np.zeros((3,3,3),'uint8'),bad)
        with self.assertRaises(ValueError):text_restore.recognize(np.zeros((20,30),'uint8'),bad)

    def test_font_proposals_require_agreement_and_exclude_sensitive_tokens(self):
        source=np.full((60,200,3),255,'uint8');source[15:30,20:90]=30
        mask=np.zeros((60,200),bool);mask[10:35,40:50]=True
        ocr={'lines':[{'words':[dict(text='degreeg',x=20,y=15,w=70,h=15)]}]}
        fitted=dict(score=.97,gray=np.full((19,74),255,'uint8'),box=[18,13,92,32],text='degrees',font='times.ttf')
        with patch.object(text_restore,'recognize',side_effect=[dict(text='degrees',confidence=.99),dict(text='degreer',confidence=.99)]),patch.object(text_restore,'fit',return_value=fitted):
            accepted,pending=text_restore.proposals(source,source,mask,ocr,ocr,self.root/'unused')
        self.assertFalse(accepted);self.assertEqual(pending[0]['suggested_text'],'degrees')
        with patch.object(text_restore,'recognize',return_value=dict(text='degrees',confidence=.99)),patch.object(text_restore,'fit',return_value=dict(fitted,score=.935)):
            accepted,pending=text_restore.proposals(source,source,mask,ocr,ocr,self.root/'unused')
        self.assertFalse(accepted);self.assertFalse(pending)
        with patch.object(text_restore,'recognize',return_value=dict(text='apphed',confidence=.99)),patch.object(text_restore,'fit') as fitting:
            accepted,pending=text_restore.proposals(source,source,mask,ocr,ocr,self.root/'unused',vocabulary={'applied'})
        self.assertFalse(accepted);self.assertFalse(pending);fitting.assert_not_called()
        for text in ('12345','Smith'):
            with patch.object(text_restore,'recognize',return_value=dict(text=text,confidence=1)):
                accepted,pending=text_restore.proposals(source,source,mask,ocr,ocr,self.root/'unused')
            self.assertFalse(accepted)
        with self.assertRaises(InterruptedError):text_restore.proposals(source,source,mask,ocr,ocr,self.root/'unused',cancelled=lambda:True)

    def test_ai_tiles_preserve_unselected_pixels_and_do_not_overwrite(self):
        image=np.random.default_rng(8).integers(0,256,(801,903,3),dtype='uint8')
        mask=np.zeros(image.shape[:2],bool)
        mask[0:800:9,:]=True;mask[:,380:390]=True;mask[-1,-1]=True
        original=image.copy();seen=[]
        def prediction(crop,selected):
            self.assertLessEqual(max(crop.shape[:2]),512)
            self.assertEqual(crop.shape[0]%8,0);self.assertEqual(crop.shape[1]%8,0)
            seen.append(crop.shape);return np.full_like(crop,123)
        result=neural_restore.inpaint(image,mask,predict=prediction)
        np.testing.assert_array_equal(image,original)
        np.testing.assert_array_equal(result[~mask],original[~mask])
        self.assertTrue((result[mask]==123).all());self.assertGreater(len(seen),1)

    def test_ai_empty_invalid_and_cancelled_are_atomic(self):
        image=np.full((40,40,3),255,'uint8');mask=np.zeros((40,40),bool)
        def fail(*args):raise AssertionError('Unexpected model call')
        np.testing.assert_array_equal(neural_restore.inpaint(image,mask,predict=fail),image)
        mask[10:20,10:20]=True
        with self.assertRaises(InterruptedError):neural_restore.inpaint(image,mask,predict=fail,cancelled=lambda:True)
        with self.assertRaises(ValueError):neural_restore.inpaint(image,mask,predict=lambda a,m:np.zeros((3,3),dtype='uint8'))
        self.assertTrue((image==255).all())

    def test_ai_model_rejects_missing_and_corrupt_weights(self):
        with self.assertRaises(ValueError):neural_restore.verify_model(self.root/'missing.pt')
        bad=self.root/'bad.pt';bad.write_bytes(b'not a model')
        with self.assertRaises(ValueError):neural_restore.verify_model(bad)

    def test_purescan_recovers_known_multiply_blend(self):
        background=np.full((40,100,3),255,'uint8');background[10:30,20:80]=0
        template=np.full_like(background,255);template[15:25,30:70]=150
        observed=np.rint(background.astype(float)*template/255).astype('uint8')
        learned=extract_template(np.stack((observed,template)))
        np.testing.assert_array_equal(invert_multiply(observed,learned),background)

    def test_purescan_protects_scope_existing_edits_and_dark_ink(self):
        source=np.full((30,60,3),150,'uint8');current=source.copy()
        current[10:15,20:30]=250
        source[2:5,2:5]=15;current[2:5,2:5]=15
        template=np.full_like(source,150);template[20:25,20:25]=0
        scope=np.zeros(source.shape[:2],bool);scope[:25,:40]=True
        result,count=scan_inversion.guarded_invert(source,current,template,scope)
        self.assertGreater(count,0)
        np.testing.assert_array_equal(result[~scope],current[~scope])
        np.testing.assert_array_equal(result[10:15,20:30],current[10:15,20:30])
        np.testing.assert_array_equal(result[2:5,2:5],current[2:5,2:5])
        np.testing.assert_array_equal(result[20:25,20:25],current[20:25,20:25])
        self.assertTrue(np.all(result[15:20,20:30]==255))

    def test_purescan_insufficient_samples_and_cancel(self):
        template=self.root/'template.npy';np.save(template,np.full((12,30),200,'uint8'))
        images=[(i,Image.new('RGB',(200,200),'white')) for i in range(3)]
        match=dict(x=20,y=20,template=np.full((12,30),200,'uint8'))
        with patch.object(scan_inversion,'known_alignment',return_value=(match,.9)):
            model=scan_inversion.learn_template(images,template)
            self.assertIsNone(model['template']);self.assertEqual(model['samples'],3)
            with self.assertRaises(InterruptedError):
                scan_inversion.learn_template(images,template,lambda:True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='clearpage-test-')
        self.root = Path(self.temp.name)
        self.pdf = self.root/'input.pdf'
        fixture(self.pdf)

    def tearDown(self):
        self.temp.cleanup()

    def test_cleanup_only_changes_selection(self):
        im = engine.render_page(self.pdf, 0)
        mask = np.zeros((im.height, im.width), bool)
        mask[300:332, 195:405] = True
        result, stats = engine.clean(im, mask, 'strong')
        self.assertGreater(stats['changed'], 0)
        self.assertTrue(np.array_equal(result[~mask], np.asarray(im)[~mask]))

    def test_light_preserves_dark_strokes(self):
        image = Image.new('RGB', (400, 300), 'white')
        draw = ImageDraw.Draw(image)
        draw.line((10, 150, 390, 150), fill='black', width=5)
        mask = np.zeros((300, 400), bool)
        mask[130:170, 180:220] = True
        result, stats = engine.clean(image, mask, 'light')
        dark = np.asarray(image).min(axis=2) < 180
        self.assertTrue(np.array_equal(result[dark], np.asarray(image)[dark]))

    def test_export_geometry_and_pixels(self):
        edits = {}
        expected = []
        for index in range(4):
            image = engine.render_page(self.pdf, index)
            before = self.root/f'{index}-before.png'
            after = self.root/f'{index}-after.png'
            image.save(before)
            arr = np.array(image)
            arr[100:150, 80:130] = [44, 110, 66]
            Image.fromarray(arr).save(after)
            edits[index] = (before, after)
            expected.append(arr)
        result = self.root/'output.pdf'
        engine.write_pdf(self.pdf, result, edits)
        self.assertEqual(len(PdfReader(result).pages), 4)
        for index in range(4):
            rendered = np.array(engine.render_page(result, index))
            self.assertTrue(np.array_equal(rendered, expected[index]), f'Page {index+1} render mismatch')

    def test_selective_export_leaves_original_pages(self):
        image = engine.render_page(self.pdf, 0)
        before, after = self.root/'before.png', self.root/'after.png'
        image.save(before)
        arr = np.array(image)
        arr[100:150, 100:150] = 255
        Image.fromarray(arr).save(after)
        output = self.root/'selective.pdf'
        engine.write_pdf(self.pdf, output, {0: (before, after)})
        for index in (1, 2, 3):
            self.assertTrue(np.array_equal(np.array(engine.render_page(self.pdf, index)),
                                           np.array(engine.render_page(output, index))))

    def test_invalid_stroke_rejected(self):
        with self.assertRaises(ValueError):
            engine.paint_mask((300, 400), [{'points': [[float('nan'), 0]], 'width': .03}])
        with self.assertRaises(ValueError):
            engine.paint_mask((300, 400), [{'points': [[2, 0]], 'width': .03}])

    def test_legacy_strong_no_longer_erases_crossing_strokes(self):
        image = Image.new('RGB', (400, 300), 'white')
        draw = ImageDraw.Draw(image)
        draw.line((10, 150, 390, 150), fill='black', width=5)
        mask = np.zeros((300, 400), bool)
        mask[130:170, 180:220] = True
        result, _ = engine.clean(image, mask, 'strong')
        dark = np.asarray(image).min(axis=2) < 180
        self.assertTrue(np.array_equal(result[dark], np.asarray(image)[dark]))

    def test_reviewed_repairs_only_apply_to_exact_source(self):
        image = engine.render_page(self.pdf, 0)
        array = np.array(image)
        array[300:332, 195:405] = 255
        reviewed = Image.fromarray(array)
        allowed = np.zeros(array.shape[:2], bool)
        allowed[300:332, 195:405] = True
        target = self.root/(engine.page_fingerprint(image)+'.npz')
        engine.save_reviewed_patch(image, reviewed, allowed, target)
        self.assertTrue(np.array_equal(engine.load_reviewed_patch(image, self.root), array))
        different = np.array(image)
        different[0, 0] = 0
        self.assertIsNone(engine.load_reviewed_patch(Image.fromarray(different), self.root))
        array[20:25, 20:25] = 0
        with self.assertRaises(ValueError):
            engine.save_reviewed_patch(image, Image.fromarray(array), allowed, target)

    def test_no_watermark_is_skipped(self):
        image = Image.new('RGB', (1192, 1684), 'white')
        mask, score = engine.known_mask(image, application.BASE/'assets/huntington.npy')
        self.assertFalse(mask.any())

    def test_word_boxes_and_text_are_explicit(self):
        for box in ([.1,.1,.1,.2], [0,0,1,1], [0,float('nan'),.2,.2], [-1,0,.2,.2]):
            with self.assertRaises(ValueError):
                word_repair.rectangle(box,(600,800))
        for text in ('',None,'one\ntwo'):
            with self.assertRaises(ValueError):
                word_repair.confirmed_text(text)

    def test_donor_pixels_and_exact_ocr_matches(self):
        image=Image.new('RGB',(600,800),'white')
        ImageDraw.Draw(image).text((60,80),'Township',fill='black')
        data=dict(text='Township',method='donor',donor_rect=[.1,.1,.25,.125])
        patch,_=word_repair.make_patch(image,(180,160,270,180),data,image)
        self.assertTrue(np.array_equal(np.array(patch),np.array(image.crop((60,80,150,100)))))
        ocr={'lines':[{'words':[dict(text='Township',x=60,y=80,w=88,h=16),dict(text='township',x=200,y=80,w=88,h=16)]}]}
        matches=word_repair.exact_matches(ocr,'Township',image.size)
        self.assertEqual(len(matches),1)
        mask=np.zeros((800,600),bool);mask[80,70]=True
        self.assertFalse(word_repair.exact_matches(ocr,'Township',image.size,mask))

    def test_automatic_reconstruction_uses_surviving_strokes(self):
        image=Image.new('L',(600,800),255)
        draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/times.ttf',30)
        words=[]
        for y in (100,200):
            draw.text((100,y),'mortgage',font=font,fill=0)
            x0,y0,x1,y1=draw.textbbox((100,y),'mortgage',font=font)
            words.append(dict(text='mortgage',x=x0,y=y0,w=x1-x0,h=y1-y0))
        clean=np.array(image);mask=np.zeros(clean.shape,bool)
        mask[190:245,140:150]=True
        damaged=clean.copy();damaged[mask]=70
        ocr={'lines':[{'words':[w]} for w in words]}
        index=auto_restore.make_index([(0,damaged,mask,ocr)])
        proposals,remaining=auto_restore.proposals(damaged,damaged,mask,ocr,ocr,index)
        self.assertTrue(proposals)
        p=proposals[0];self.assertEqual(p['text'],'mortgage');self.assertGreater(p['score'],.99)
        x,y,xx,yy=p['box'];self.assertLess(np.abs(p['gray'].astype(float)-clean[y:yy,x:xx]).mean(),4)

    def test_automatic_reconstruction_does_not_invent_names_or_numbers(self):
        for text in ('123.45','Avery','Surname','mortg4ge'):
            word=dict(text=text,x=10,y=10,w=80,h=20)
            gray=np.full((100,200),255,'uint8');mask=np.zeros_like(gray,bool);mask[15:20,20:30]=True
            ocr={'lines':[{'words':[word]}]}
            picks,pending=auto_restore.proposals(gray,gray,mask,ocr,ocr,{})
            self.assertFalse(picks);self.assertTrue(pending)
        for a,b,wanted in [('kitten','sitting',3),('abc','abc',0),('said','sfid',1),('abc','xyz',3)]:
            self.assertEqual(auto_restore.distance(a,b),wanted)

    def test_residual_cleanup_preserves_words_and_stays_in_corridor(self):
        image=Image.new('RGB',(600,300),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/times.ttf',20);items=[]
        for x in (10,100,190,280,370):
            draw.text((x,100),'text',font=font,fill='black')
            xx,y,xxx,yy=draw.textbbox((x,100),'text',font=font)
            items.append(dict(text='text',x=xx,y=y,w=xxx-xx,h=yy-y))
        draw.rectangle((151,108,158,113),fill='black')
        draw.rectangle((151,96,158,99),fill='black')
        array=np.array(image);mask=np.zeros(array.shape[:2],bool);mask[90:116,148:160]=True
        correction=auto_restore.background_patch(array,mask,{'lines':[{'words':items}]})
        self.assertGreater(len(correction['indices']),0)
        selected=np.zeros(mask.size,bool);selected[correction['indices']]=True;selected=selected.reshape(mask.shape)
        for w in items:self.assertFalse(selected[w['y']:w['y']+w['h'],w['x']:w['x']+w['w']].any())
        self.assertFalse(selected[108:114,151:159].any(),'Protect unrecognized words inside the text-line band')
        self.assertFalse(selected[~mask].any())


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.temp.name)/'test.pdf'
        fixture(self.pdf)
        self.client = application.app.test_client()
        self.headers = {'X-ClearPage': 'local'}
        application.PROFILE_ROOT = Path(self.temp.name)/'profiles'

    def tearDown(self):
        for identifier, job in list(application.JOBS.items()):
            while job['state'] in ('processing', 'exporting', 'learning'):
                time.sleep(.05)
            self.client.post('/api/clear/'+identifier, json={}, headers=self.headers)
        self.temp.cleanup()

    def test_local_access_without_key_and_input_validation(self):
        fresh_browser = application.app.test_client(use_cookies=False)
        with fresh_browser.get('/') as response:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('Set-Cookie', response.headers)
        self.assertEqual(fresh_browser.get('/api/session').status_code, 200)
        self.assertEqual(fresh_browser.get('/', environ_overrides={'REMOTE_ADDR': '192.0.2.1'}).status_code, 403)
        self.assertEqual(fresh_browser.get('/api/session', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        with patch.object(application, 'HOST', '127.0.0.1:5055'):
            self.assertEqual(fresh_browser.get('/api/session', headers={'Host': 'attacker.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/upload', data=b'%PDF').status_code, 403)
        response = self.client.post('/api/upload', data=b'not a PDF', headers=self.headers)
        self.assertEqual(response.status_code, 400)
        headers = dict(self.headers, Origin='https://example.com')
        self.assertEqual(self.client.post('/api/upload', data=b'%PDF', headers=headers).status_code, 403)

    def test_react_assets_use_local_routes_without_cookies(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/static/ui.js', response.data)
        self.assertIn(b'/static/ui.css', response.data)
        response.close()
        fresh_browser = application.app.test_client(use_cookies=False)
        for asset, content_type in [('ui.js', 'javascript'), ('ui.css', 'text/css')]:
            response = fresh_browser.get('/static/' + asset)
            self.assertEqual(response.status_code, 200)
            self.assertIn(content_type, response.content_type)
            self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            response.close()
        self.assertEqual(self.client.get('/static/package.json').status_code, 404)

    def test_purescan_preview_learning_and_fallback(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        options=dict(method='preset',scan_inversion=True,auto_restore=False)
        self.assertEqual(self.client.post('/api/preview/'+identifier,json=options,headers=self.headers).status_code,400)
        self.client.post('/api/process/'+identifier,json=options,headers=self.headers);self.wait(identifier,'review')
        job=application.JOBS[identifier]
        self.assertIsNone(job['inversion_model']['template'])
        self.assertTrue(all(r['inverted']==0 and 'found 0' in r['inversion_note'] for r in job['results']))
        self.assertEqual(self.client.post('/api/preview/'+identifier,json=options,headers=self.headers).status_code,200)
        options.update(method='manual',strokes=[dict(points=[[.2,.3]],width=.03)])
        self.assertEqual(self.client.post('/api/process/'+identifier,json=options,headers=self.headers).status_code,400)

    def test_upload_preview_batch_export_clear(self):
        response = self.client.post('/api/upload', data=self.pdf.read_bytes(), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        job = response.json
        identifier = job['id']
        self.assertEqual(job['count'], 4)
        options = dict(method='manual', mode='strong', page=0,
                       strokes=[dict(points=[[.33, .39], [.67, .39]], width=.07)])
        response = self.client.post('/api/preview/'+identifier, json=options, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json['changed'], 0)
        self.assertEqual(self.client.get(f'/api/page/{identifier}/999/before').status_code, 400)
        response = self.client.post('/api/process/'+identifier, json=options, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.wait(identifier, 'review')
        response = self.client.post('/api/export/'+identifier, json={'keep': [0]}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.wait(identifier, 'done')
        response = self.client.get('/api/download/'+identifier)
        self.assertEqual(response.status_code, 200)
        result = PdfReader(BytesIO(response.data))
        self.assertEqual(len(result.pages), 4)
        response.close()
        self.assertEqual(self.client.post('/api/clear/'+identifier, json={}, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/job/'+identifier).status_code, 400)

    def wait(self, identifier, target):
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            status = self.client.get('/api/job/'+identifier).json
            if status['state'] == target:
                return
            self.assertNotEqual(status['state'], 'error')
            time.sleep(.05)
        self.fail('Operation timed out')

    def test_word_preview_apply_reprocess_export_and_undo(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        prefix=f'/api/repair/{identifier}'
        options=dict(method='manual',mode='careful',strokes=[dict(points=[[.4,.4],[.6,.4]],width=.04)])
        payload=dict(page=0,rect=[.1,.2,.3,.23],text='CONFIRMED',method='text',font='serif',darkness=90)
        self.assertEqual(self.client.post(prefix+'/preview',json=payload,headers=self.headers).status_code,400)
        self.client.post('/api/process/'+identifier,json=options,headers=self.headers)
        self.wait(identifier,'review')
        job=application.JOBS[identifier]
        path=application.image_path(job,0,True)
        before=np.array(Image.open(path))
        response=self.client.post(prefix+'/preview',json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        token=response.json['token']
        self.assertTrue(np.array_equal(before,np.array(Image.open(path))), 'Preview must not change the page')
        self.assertEqual(self.client.post(prefix+'/apply',json=dict(page=0,token=token,confirmed=False),headers=self.headers).status_code,400)
        response=self.client.post(prefix+'/apply',json=dict(page=0,token=token,confirmed=True),headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        after=np.array(Image.open(path))
        changed=np.any(after!=before,axis=2)
        self.assertGreater(changed.sum(),0)
        allowed=np.zeros(changed.shape,bool);allowed[160:184,60:180]=True
        self.assertFalse(changed[~allowed].any(), 'A word repair must not touch its neighbours')
        self.assertEqual(self.client.post(prefix+'/apply',json=dict(page=0,token=token,confirmed=True),headers=self.headers).status_code,400)
        self.client.post('/api/process/'+identifier,json=options,headers=self.headers)
        self.wait(identifier,'review')
        self.assertTrue(np.array_equal(after,np.array(Image.open(path))), 'Repairs must survive reprocessing')
        self.client.post('/api/export/'+identifier,json={'keep':[0]},headers=self.headers)
        self.wait(identifier,'done')
        self.assertTrue(np.array_equal(after,np.array(engine.render_page(job['dir']/'cleaned.pdf',0))), 'Export must reproduce repaired pixels')
        log=self.client.get(prefix+'/history')
        self.assertEqual(log.json['repairs'][0]['confirmed_text'],'CONFIRMED');log.close()
        response=self.client.post(prefix+'/undo',json={'page':0},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertTrue(np.array_equal(before,np.array(Image.open(path))), 'Undo must restore every pixel')

    def test_ai_batch_cache_and_targeted_preview(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        options=dict(method='manual',mode='lama',strokes=[dict(points=[[.4,.4],[.6,.4]],width=.04)])
        def fake(image,mask,*args,**kwargs):
            result=image.copy();result[mask>0]=[211,212,213];return result
        with patch.object(neural_restore,'inpaint',side_effect=fake) as inference:
            self.client.post('/api/process/'+identifier,json=options,headers=self.headers);self.wait(identifier,'review')
            job=application.JOBS[identifier]
            self.assertTrue(all(r['ai'] and r['uncertain'] for r in job['results']))
            calls=inference.call_count
            application.process_page(job,0,job['options'])
            self.assertEqual(inference.call_count,calls,'Raw AI predictions should be reused')
            # Targeted candidate changes only mask intersected with the chosen word.
            prefix=f'/api/repair/{identifier}'
            data=dict(page=0,rect=[.38,.38,.62,.43],text='CONFIRMED',method='lama')
            before=application.image_path(job,0,True).read_bytes()
            response=self.client.post(prefix+'/preview',json=data,headers=self.headers)
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(before,application.image_path(job,0,True).read_bytes())
            self.assertIn('not a model instruction',response.json['message'])
            self.assertEqual(self.client.post(prefix+'/apply',json=dict(page=0,token=response.json['token'],confirmed=True),headers=self.headers).status_code,200)
            self.assertEqual(job['repairs'][0][-1]['method'],'lama')

    def test_stale_word_preview_rejected(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        options=dict(method='preset',mode='careful')
        self.client.post('/api/process/'+identifier,json=options,headers=self.headers)
        self.wait(identifier,'review')
        prefix=f'/api/repair/{identifier}'
        payload=dict(page=0,rect=[.1,.2,.3,.23],text='checked',method='text')
        token=self.client.post(prefix+'/preview',json=payload,headers=self.headers).json['token']
        path=application.image_path(application.JOBS[identifier],0,True)
        im=Image.open(path).convert('RGB');im.putpixel((0,0),(0,0,0));im.save(path)
        response=self.client.post(prefix+'/apply',json=dict(page=0,token=token,confirmed=True),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertIn('page changed',response.json['error'])

    def test_text_read_and_fitted_preview_are_nonmutating_apply_undo(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        options=dict(method='manual',mode='careful',strokes=[dict(points=[[.4,.4],[.6,.4]],width=.04)])
        self.client.post('/api/process/'+identifier,json=options,headers=self.headers);self.wait(identifier,'review')
        job=application.JOBS[identifier];path=application.image_path(job,0,True);before=path.read_bytes()
        prefix=f'/api/repair/{identifier}';payload=dict(page=0,rect=[.38,.38,.62,.43],text='checked',method='fit')
        box=word_repair.rectangle(payload['rect'],Image.open(path).size)
        with patch.object(text_restore,'recognize',return_value=dict(text='checked',confidence=.99)):
            response=self.client.post(prefix+'/read',json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['original']['text'],'checked')
        self.assertEqual(path.read_bytes(),before)
        with patch.object(text_restore,'fit',return_value=dict(score=.97,gray=np.zeros((box[3]-box[1],box[2]-box[0]),'uint8'))):
            response=self.client.post(prefix+'/preview',json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.client.post(prefix+'/apply',json=dict(page=0,token=response.json['token'],confirmed=True),headers=self.headers).status_code,200)
        self.assertEqual(job['repairs'][0][-1]['method'],'fit')
        self.assertEqual(self.client.post(prefix+'/undo',json=dict(page=0),headers=self.headers).status_code,200)
        self.assertEqual(path.read_bytes(),before)

    def test_automatic_font_fit_is_logged_and_reversible(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        self.client.post('/api/process/'+identifier,json=dict(method='manual',mode='careful',strokes=[dict(points=[[.4,.4],[.6,.4]],width=.04)]),headers=self.headers)
        self.wait(identifier,'review');job=application.JOBS[identifier]
        path=application.image_path(job,0,True);before=path.read_bytes()
        candidate=dict(box=[200,300,240,320],gray=np.zeros((20,40),'uint8'),text='matching',score=.99,font='times.ttf',method='font-fit')
        with patch.object(application,'local_ocr',return_value={'lines':[]}),patch.object(text_restore,'proposals',side_effect=lambda *a,**k:([dict(candidate)],[])):
            application.restoration_worker(job)
        self.assertEqual(job['results'][0]['fitted_words'],1)
        history=self.client.get(f'/api/repair/{identifier}/history').json
        self.assertEqual(history['repairs'][0]['method'],'font-fit');self.assertIsNone(history['repairs'][0]['donor_page'])
        application.restoration_worker(job,clear=True);self.assertEqual(path.read_bytes(),before)

    def test_automatic_batch_apply_and_undo_retains_manual_repairs(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        options=dict(method='manual',mode='careful',strokes=[dict(points=[[.4,.4],[.6,.4]],width=.04)])
        self.client.post('/api/process/'+identifier,json=options,headers=self.headers);self.wait(identifier,'review')
        job=application.JOBS[identifier];path=application.image_path(job,0,True)
        base=np.array(Image.open(path));manual=Image.new('RGB',(20,12),'blue')
        job['repairs']={0:[dict(box=[20,20,40,32],patch=manual,text='checked',method='text')]}
        with patch.object(application,'local_ocr',return_value={'lines':[]}),patch.object(auto_restore,'proposals',return_value=([
            dict(box=[200,300,240,320],gray=np.zeros((20,40),'uint8'),text='matching',donor_page=0,donor_box=[10,10,50,30],score=.99)
        ],[])):
            application.restoration_worker(job)
        self.assertEqual(job['state'],'review');self.assertEqual(job['results'][0]['automatic'],1)
        arr=np.array(Image.open(path));self.assertTrue((arr[300:320,200:240]==0).all())
        self.assertTrue((arr[20:32,20:40]==[0,0,255]).all())
        application.restoration_worker(job,clear=True)
        expected=base.copy();expected[20:32,20:40]=[0,0,255]
        self.assertTrue(np.array_equal(np.array(Image.open(path)),expected))
        with patch.object(application,'local_ocr',return_value={'lines':[]}),patch.object(auto_restore,'proposals',return_value=([
            dict(box=[200,300,240,320],gray=np.zeros((20,40),'uint8'),text='of',ocr_text='or',donor_page=0,
                 donor_box=[10,10,50,30],donor_rect=[.01,.01,.1,.1],score=.99,needs_confirmation=True)
        ],[])):
            application.restoration_worker(job)
        self.assertEqual(job['results'][0]['automatic'],0)
        self.assertEqual(job['results'][0]['overlaps'][0]['suggested_text'],'of')
        self.assertTrue(np.array_equal(np.array(Image.open(path)),expected),'A proposed wording change must not be applied automatically')

    def test_automatic_cancel_leaves_existing_pixels_unchanged(self):
        identifier=self.client.post('/api/upload',data=self.pdf.read_bytes(),headers=self.headers).json['id']
        self.client.post('/api/process/'+identifier,json=dict(method='preset'),headers=self.headers);self.wait(identifier,'review')
        job=application.JOBS[identifier];path=application.image_path(job,0,True);before=path.read_bytes()
        job['cancel']=True;application.restoration_worker(job)
        self.assertEqual(job['state'],'review');self.assertEqual(before,path.read_bytes());self.assertFalse(job.get('automatic'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
