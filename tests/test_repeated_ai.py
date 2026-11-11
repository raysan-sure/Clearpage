"""Local reconstruction routing, masking, cache, and explicit review checks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
import app
from clearpage.pdf import engine
from clearpage.presets import scan_patterns
from tests.test_scan_patterns import repeated_fixture
from tests.test_repeated_document import document_fixture


class RepeatedAITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'input.pdf';self.a,self.clean=document_fixture()
        canvas=Canvas(str(self.source),pagesize=(600,900))
        for _ in range(2):
            canvas.drawImage(ImageReader(Image.fromarray(self.a)),0,0,600,900);canvas.showPage()
        canvas.save()
        self.client=app.app.test_client()
        with self.client.get('/'):pass
        self.headers={'X-ClearPage':'local'}
        response=self.client.post('/api/upload',data=self.source.read_bytes(),headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        self.job=app.JOBS[response.json['id']];self.key=self.job['id']
        self.options=app.settings(dict(method='repeated_diagonal_ai',auto_restore=True,fix_negative=True,use_reviewed=True))

    def tearDown(self):
        self.job['state']='ready'
        self.client.post('/api/clear/'+self.key,json={},headers=self.headers)
        self.tmp.cleanup()

    @staticmethod
    def predict(source,mask,*args,**kwargs):
        result=source.copy();result[mask]=255
        return result

    def test_full_mask_reaches_overlaps_and_separate_source_strokes_are_known_ink(self):
        mask,retained,note=scan_patterns.repeated_ai_plan(Image.fromarray(self.a))
        self.assertTrue(mask[400:950,150:1050].any())
        self.assertGreater(int(retained.sum()),1000)
        self.assertFalse(retained[self.clean.min(2)==255].any())
        self.assertFalse(retained[~mask].any())
        self.assertFalse(mask.all());self.assertIn('draft',note)
        for image in (Image.new('RGB',(1200,1800),'white'),Image.new('RGB',(300,300),'black')):
            mask,_=scan_patterns.repeated_ai_mask(image);self.assertFalse(mask.any())
        with self.assertRaises(InterruptedError):
            scan_patterns.repeated_ai_mask(Image.fromarray(self.a),lambda:True)

    def test_missing_glyph_template_retains_earlier_full_cleanup_coverage(self):
        # Glyph learning is an optional source-copy step. Its failure must not
        # silently skip watermark areas as the previous narrow-mask version did.
        with patch.object(app.neural_restore,'inpaint',side_effect=self.predict) as model:
            Image.fromarray(repeated_fixture()).save(app.original(self.job,0))
            result=app.process_page(self.job,0,self.options)
        self.assertTrue(result['matched']);self.assertTrue(result['reconstruction_unverified'])
        self.assertEqual(result['retained_source_pixels'],0)
        model.assert_called_once()

    def test_template_preserves_dark_ink_at_multiple_supported_resolutions(self):
        import cv2
        for factor in (1.,1.5):
            size=(round(1200*factor),round(1800*factor))
            a=np.array(Image.fromarray(self.a).resize(size,Image.Resampling.NEAREST))
            clean=np.array(Image.fromarray(self.clean).resize(size,Image.Resampling.NEAREST))
            selected,retained,_=scan_patterns.repeated_ai_plan(Image.fromarray(a))
            self.assertGreater(int(selected.sum()),1000)
            band=scan_patterns._repeated_detection(Image.fromarray(a))[4]
            broad=(cv2.dilate((band&(a.min(2)<245)).astype('uint8'),np.ones((3,3),'uint8'))>0)&band
            dark=clean.min(2)<100
            self.assertGreater(int((broad&dark).sum()),1000)
            # Exactly retain v1 removal coverage, including unsupported areas.
            np.testing.assert_array_equal(selected,broad)
            self.assertGreater(int((retained&dark).sum()),int((broad&dark).sum())*.5)
            self.assertFalse(retained[clean.min(2)==255].any())
            faint=(a.min(2)>=100)&(a.min(2)<230)&(clean.min(2)<230)
            self.assertGreater(int((retained&faint).sum()),200)
            # Faint copy-back must preserve the known clean pixel, including
            # antialiasing, rather than copy an overlapping watermark pixel.
            np.testing.assert_array_equal(a[retained&faint],clean[retained&faint])
            output=self.predict(a,selected);output[retained]=a[retained]
            np.testing.assert_array_equal(output[retained],a[retained])

    def test_faint_copy_requires_three_clear_neighborhoods(self):
        from clearpage.presets import repeated_template
        gray=np.full((80,220),255,'uint8')
        gray[40,100]=180
        gray[10:15,10:15]=150
        donors=np.zeros(gray.shape,bool);donors[10:15,10:15]=True
        content=np.zeros(gray.shape,bool);content[35:46,95:106]=True
        broad=content.copy();band=np.ones(gray.shape,bool)
        def copy(vector):
            with patch.object(repeated_template,'repeat_vector',return_value=vector):
                return repeated_template.retained_source_strokes(gray,broad,donors,content,band)[0]
        self.assertTrue(copy((20,0))[40,100])
        # Only two independent references exist at x=20 and x=180.
        self.assertFalse(copy((80,0))[40,100])
        # A watermark beside a reference must veto even when the exact
        # reference pixel is white and every other copy shows white.
        gray[40,81]=150;donors[40,81]=True
        self.assertFalse(copy((20,0))[40,100])

    def test_isolated_dark_watermark_never_copied_back_as_source_ink(self):
        from clearpage.presets import repeated_template
        gray=np.full((80,80),255,'uint8');gray[10:15,10:15]=20
        donors=gray<100;broad=donors.copy()
        # A clipped donor can be outside the coverage of all other copies.
        with patch.object(repeated_template,'repeat_vector',return_value=(100,100)):
            retained,learned=repeated_template.retained_source_strokes(gray,broad,donors,np.zeros(gray.shape,bool),np.ones(gray.shape,bool))
        self.assertTrue(learned)
        self.assertFalse(retained.any())

    def test_app_copies_only_supported_source_strokes_and_rejects_corrupt_cache(self):
        before=np.array(Image.open(app.original(self.job,0)).convert('RGB'))
        selected,retained,_=scan_patterns.repeated_ai_plan(Image.fromarray(before))
        self.assertGreater(int(retained.sum()),1000)
        baseline=self.predict(before,selected)
        with patch.object(app.neural_restore,'inpaint',side_effect=self.predict) as model:
            result=app.process_page(self.job,0,self.options)
        np.testing.assert_array_equal(model.call_args.args[1],selected)
        with Image.open(app.image_path(self.job,0,True)) as image:after=np.array(image)
        np.testing.assert_array_equal(after[~retained],baseline[~retained])
        np.testing.assert_array_equal(after[retained],before[retained])
        self.assertEqual(result['retained_source_pixels'],int(retained.sum()))
        cache=next(self.job['dir'].glob('*-repeated-ai-*.png'))
        y,x=np.argwhere(retained)[0];after[y,x]=255
        Image.fromarray(after).save(cache)
        with self.assertRaisesRegex(ValueError,'original-stroke'):
            app.process_page(self.job,0,self.options)

    def test_preview_cache_batch_review_gate_and_selective_export(self):
        self.assertEqual(self.options['mode'],'lama')
        self.assertFalse(self.options['auto_restore']);self.assertFalse(self.options['fix_negative'])
        self.assertFalse(self.options['use_reviewed'])
        with patch.object(app.neural_restore,'inpaint',side_effect=self.predict) as model:
            first=self.client.post('/api/preview/'+self.key,json=dict(method='repeated_diagonal_ai',page=0),headers=self.headers)
            self.assertEqual(first.status_code,200,first.json)
            self.assertTrue(first.json['ai']);self.assertTrue(first.json['uncertain'])
            self.assertTrue(first.json['reconstruction_unverified'])
            again=self.client.post('/api/preview/'+self.key,json=dict(method='repeated_diagonal_ai',page=0),headers=self.headers)
            self.assertEqual(first.json,again.json);self.assertEqual(model.call_count,1)
            self.job.update(options=self.options,results=[],cancel=False)
            app.batch(self.job,self.options)
            self.assertEqual(self.job['state'],'review',self.job['message'])
            self.assertEqual(model.call_count,2)
        denied=self.client.post('/api/export/'+self.key,json={'keep':[0]},headers=self.headers)
        self.assertEqual(denied.status_code,400);self.assertIn('Mark page reviewed',denied.json['error'])
        app.require_ai_review(self.job,[])  # Keeping originals requires no AI approval.
        self.job['ui']={'reviewed':[1]}
        with self.assertRaises(ValueError):app.require_ai_review(self.job,[0])
        self.job['ui']={'reviewed':[0]}
        app.export_worker(self.job,[0])
        self.assertEqual(self.job['state'],'done',self.job['message'])
        for i in range(2):
            rendered=engine.render_page(self.job['dir']/'cleaned.pdf',i)
            expected=Image.open(app.image_path(self.job,0,True)) if i==0 else engine.render_page(self.source,1)
            self.assertTrue(np.array_equal(rendered,expected))
        # Returning to conservative mode must start from the original, not AI pixels.
        options=app.settings({'method':'repeated_diagonal'})
        stats=app.process_page(self.job,0,options)
        x0,y0,x1,y1=stats['protected_box']
        after=np.array(Image.open(app.image_path(self.job,0,True)))
        original=np.array(engine.render_page(self.source,0))
        self.assertTrue(np.array_equal(after[y0:y1,x0:x1],original[y0:y1,x0:x1]))

    def test_model_failure_and_cancel_do_not_publish_draft(self):
        for error in (ValueError('weights missing'),InterruptedError('Cancelled')):
            with patch.object(app.neural_restore,'inpaint',side_effect=error):
                with self.assertRaises(type(error)):
                    app.process_page(self.job,0,self.options)
            self.assertFalse(app.image_path(self.job,0,True).exists())
            self.assertFalse(list(self.job['dir'].glob('*-repeated-ai-*.png')))

    def test_invalid_model_output_is_rejected(self):
        with patch.object(app.neural_restore,'inpaint',return_value=np.zeros_like(self.a)):
            with self.assertRaisesRegex(ValueError,'pixel-preservation'):
                app.process_page(self.job,0,self.options)
        self.assertFalse(app.image_path(self.job,0,True).exists())
        self.assertFalse(list(self.job['dir'].glob('*-repeated-ai-*.png')))

    def test_low_memory_reports_recovery_without_publishing_a_draft(self):
        original=self.job['source'].read_bytes()
        with patch.object(app.neural_restore,'inpaint',side_effect=MemoryError('internal details')):
            response=self.client.post('/api/preview/'+self.key,json=dict(method='repeated_diagonal_ai',page=0),headers=self.headers)
            self.assertEqual(response.status_code,503)
            self.assertIn('Not enough memory',response.json['error'])
            self.assertNotIn('internal details',response.json['error'])
            self.job.update(options=self.options,results=[],cancel=False)
            app.batch(self.job,self.options)
            self.assertEqual(self.job['state'],'error')
            self.assertIn('Not enough memory',self.job['message'])
        self.assertFalse(app.image_path(self.job,0,True).exists())
        self.assertFalse(list(self.job['dir'].glob('*-repeated-ai-*.png')))
        self.assertEqual(self.job['source'].read_bytes(),original)

    def test_local_ai_availability_reports_missing_dependencies(self):
        with patch.object(app.neural_restore,'verify_model',side_effect=ValueError('LaMa weights are missing.')):
            response=self.client.get('/api/local-ai')
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json['ready'])
        with patch.object(app.neural_restore,'verify_model'), patch('importlib.util.find_spec',return_value=None):
            self.assertFalse(self.client.get('/api/local-ai').json['ready'])
        with patch.object(app.neural_restore,'verify_model'), patch('importlib.util.find_spec',return_value=object()):
            self.assertTrue(self.client.get('/api/local-ai').json['ready'])

    def test_cancelled_preview_can_be_retried(self):
        with patch.object(app.neural_restore,'inpaint',side_effect=InterruptedError()):
            response=self.client.post('/api/preview/'+self.key,json=dict(method='repeated_diagonal_ai',page=0),headers=self.headers)
            self.assertEqual(response.status_code,400)
            self.assertIn('Preview stopped',response.json['error'])
        self.job['cancel']=True
        with patch.object(app.neural_restore,'inpaint',side_effect=self.predict):
            response=self.client.post('/api/preview/'+self.key,json=dict(method='repeated_diagonal_ai',page=0),headers=self.headers)
            self.assertEqual(response.status_code,200,response.json)
            self.assertFalse(self.job['cancel'])


if __name__=='__main__':unittest.main()
