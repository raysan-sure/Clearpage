"""Scan preset preservation and full application route regressions."""
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
import app
from clearpage.pdf import engine
from clearpage.presets import scan_patterns as patterns


ASSETS = RESOURCE_ROOT/'assets'


def gray_fixture(height=1994):
    with np.load(ASSETS/f'unofficial-copy-{height}.npz') as data:
        a=data['seed'].copy()
    a[height//2:height//2+5,200:1300]=0
    a[100:108,100:800]=0
    return a


def repeated_fixture():
    a=np.full((1800,1200,3),255,'uint8')
    theta=np.pi/4
    for rho in range(180,2100,180):
        for x in range(0,1200,7):
            y=round((rho-x*np.cos(theta))/np.sin(theta))
            if 0<=y<1797:a[y:y+3,x:x+3]=150
    for y in (500,700,900):a[y:y+6,300:900]=0
    return a


def huntington_fixture():
    phrase = Image.fromarray(np.load(ASSETS/'huntington.npy', allow_pickle=False))
    phrase = phrase.rotate(45, expand=True, fillcolor=255)
    image = Image.new('RGB', (1192, 1684), 'white')
    image.paste(phrase, (200, 200))
    a = np.array(image)
    # Dark and faint body strokes cross the known watermark.
    a[600:604, 100:1050] = 0
    a[670:674, 100:1050] = 210
    a[80:84, 100:900] = 0
    return a


class ScanPatternTests(unittest.TestCase):
    def test_gray_outline_match_removes_long_fragments_but_keeps_crossing_lines(self):
        seed = Image.new('RGB', (1541, 240), 'white')
        draw = ImageDraw.Draw(seed)
        for x in (100, 400, 1000):
            draw.polygon([(x, 30), (x+120, 150), (x+140, 150), (x+20, 30)],
                         fill=(160, 160, 160))
        image = Image.new('RGB', seed.size, 'white')
        draw = ImageDraw.Draw(image)
        draw.line((100, 30, 220, 150), fill=(205, 205, 205))
        draw.line((350, 90, 600, 90), fill=(225, 225, 225))
        draw.line((700, 30, 820, 150), fill=(225, 225, 225))
        # A dark mark on a known edge must not be mistaken for an outline.
        draw.line((1000, 30, 1120, 150), fill=(80, 80, 80))
        a = np.array(image)
        selected = np.ones(a.shape[:2], bool)
        result, removed = patterns.gray_watermark_edges(a, selected, np.array(seed))
        self.assertGreater(removed, 100)
        self.assertTrue(np.all(result[:, :300] == 255))
        np.testing.assert_array_equal(result[:, 300:], a[:, 300:])
        # A contour connected to content outside the selection is retained.
        selected[30, 100] = False
        result, removed = patterns.gray_watermark_edges(a, selected, np.array(seed))
        np.testing.assert_array_equal(result, a)
        self.assertEqual(removed, 0)
        with self.assertRaises(InterruptedError):
            patterns.gray_watermark_edges(a, selected, np.array(seed), lambda: True)

    def test_gray_residue_removes_speckle_and_preserves_faint_geometry(self):
        a = np.full((240, 1541, 3), 255, 'uint8')
        selected = np.zeros(a.shape[:2], bool)
        selected[20:220, 100:1400] = True
        # Disconnected gray fragments left by compressed watermark edges.
        for y in range(40, 110, 8):
            a[y, 150:400:8] = 225
            a[y+2, 150:400:8] = 198
        # Long faint lines, a short printed stroke, and dark punctuation.
        a[150, 200:600] = 230
        a[30:210, 700] = 225
        for y in range(40, 180):
            a[y, 800+y] = 230
        a[180:183, 400:410] = 200
        a[190, 500] = 80
        # Even a short faint component crossing the selection must survive.
        a[19:23, 1100] = 230
        a[10, 50] = 225
        before = a.copy()
        result, removed = patterns.gray_watermark_residue(a, selected)
        self.assertGreater(removed, 400)
        self.assertTrue(np.all(result[40:110, 150:400] == 255))
        np.testing.assert_array_equal(result[120:], a[120:])
        np.testing.assert_array_equal(result[:, 650:], a[:, 650:])
        np.testing.assert_array_equal(result[~selected], a[~selected])
        np.testing.assert_array_equal(a, before)
        self.assertEqual(removed, np.any(result != a, axis=2).sum())

    def test_gray_residue_keeps_stroke_edges_and_handles_cancellation(self):
        a = np.full((60, 1541, 3), 255, 'uint8')
        selected = np.ones(a.shape[:2], bool)
        a[30, 500:540] = 100
        a[29, 500:540] = 235
        a[31, 500:540] = 240
        result, removed = patterns.gray_watermark_residue(a, selected)
        np.testing.assert_array_equal(result, a)
        self.assertEqual(removed, 0)
        with self.assertRaises(InterruptedError):
            patterns.gray_watermark_residue(a, selected, lambda: True)

    def test_huntington_removes_more_without_changing_known_text_pixels(self):
        a = huntington_fixture()
        lettering = Image.new('L', (1192, 1684), 255)
        draw = ImageDraw.Draw(lettering)
        font = ImageFont.load_default(size=18)
        for y in range(350, 950, 36):
            draw.text((140, y), 'Parcel 35-11-24-100.026.602-008; 22.07 feet. Recorded 11/13/2025.',
                      fill=210 if y % 72 == 26 else 25, font=font)
        text = np.array(lettering)
        a = np.minimum(a, text[:, :, None])
        image = Image.fromarray(a)
        result, _, stats = patterns.huntington_background(image, ASSETS)
        scope, _ = engine.known_mask(image, ASSETS/'huntington.npy', min_score=.55)
        _, previous = engine.clean(image, scope, 'careful')
        self.assertGreater(stats['changed'], previous['changed'])
        np.testing.assert_array_equal(result[text < 255], a[text < 255])

    def test_huntington_protects_vertical_rules_and_signature_strokes(self):
        a = huntington_fixture()
        a[300:1000, 480:484] = 90
        a[400:550, 600:603] = 200
        result, _, _ = patterns.huntington_background(Image.fromarray(a), ASSETS)
        np.testing.assert_array_equal(result[300:1000, 480:484], a[300:1000, 480:484])
        np.testing.assert_array_equal(result[400:550, 600:603], a[400:550, 600:603])

    def test_huntington_background_preserves_body_strokes_and_outside_scope(self):
        a = huntington_fixture()
        image = Image.fromarray(a)
        result, changed, stats = patterns.huntington_background(image, ASSETS)
        self.assertTrue(stats['matched'])
        self.assertTrue(stats['partial'])
        self.assertTrue(stats['uncertain'])
        self.assertGreater(stats['changed'], 1000)
        scope, _ = engine.known_mask(image, ASSETS/'huntington.npy', min_score=.55)
        self.assertFalse(changed[~scope].any())
        np.testing.assert_array_equal(result[600:604], a[600:604])
        np.testing.assert_array_equal(result[670:674], a[670:674])
        np.testing.assert_array_equal(changed, np.any(result != a, axis=2))

    def test_huntington_background_rejects_other_patterns_and_cancellation(self):
        for a in (np.full((1684,1192,3),255,'uint8'), repeated_fixture()):
            result, changed, stats = patterns.huntington_background(Image.fromarray(a), ASSETS)
            self.assertFalse(stats['matched'])
            self.assertFalse(changed.any())
            np.testing.assert_array_equal(result, a)
        with self.assertRaises(InterruptedError):
            patterns.huntington_background(Image.fromarray(huntington_fixture()), ASSETS, lambda: True)

    def test_gray_layouts_preserve_dark_strokes_and_outside_template(self):
        for height in patterns.GRAY_LAYOUTS:
            a=gray_fixture(height)
            result,mask,stats=patterns.unofficial_copy(Image.fromarray(a),ASSETS)
            self.assertTrue(stats['matched'])
            self.assertGreater(stats['changed'],1000)
            self.assertTrue(np.array_equal(result[a.min(2)<=32],a[a.min(2)<=32]))
            with np.load(ASSETS/f'unofficial-copy-{height}.npz') as data:
                self.assertFalse(mask[~data['scope']].any())
            self.assertTrue(np.array_equal(mask,np.any(a!=result,axis=2)))

    def test_gray_clean_shifted_and_wrong_aspect_are_unchanged(self):
        for a in (np.full((1994,1541,3),255,'uint8'),np.roll(gray_fixture(),180,axis=1),np.full((400,800,3),180,'uint8')):
            result,mask,stats=patterns.unofficial_copy(Image.fromarray(a),ASSETS)
            self.assertFalse(stats['matched']);self.assertFalse(mask.any())
            self.assertTrue(np.array_equal(result,a))

    def test_landscape_clean_and_displaced_patterns_are_retained(self):
        for height in (1019,1032):
            for a in (np.full((height,1541,3),255,'uint8'), np.roll(gray_fixture(height),150,axis=1)):
                result,mask,stats=patterns.unofficial_copy(Image.fromarray(a),ASSETS)
                self.assertFalse(stats['matched']);self.assertFalse(mask.any())
                np.testing.assert_array_equal(result,a)

    def test_partial_repeat_preserves_content_rectangle_exactly(self):
        a=repeated_fixture()
        result,mask,stats=patterns.repeated_diagonal(Image.fromarray(a))
        self.assertTrue(stats['matched']);self.assertTrue(stats['partial']);self.assertTrue(stats['uncertain'])
        x0,y0,x1,y1=stats['protected_box']
        self.assertLessEqual(x0,300);self.assertGreaterEqual(x1,900)
        self.assertLessEqual(y0,500);self.assertGreaterEqual(y1,906)
        self.assertTrue(np.array_equal(result[y0:y1,x0:x1],a[y0:y1,x0:x1]))
        self.assertGreater(stats['changed'],300)

    def test_repeated_clean_page_and_no_content_are_unchanged(self):
        a=np.full((1800,1200,3),255,'uint8')
        a[500:506,300:900]=0
        result,mask,stats=patterns.repeated_diagonal(Image.fromarray(a))
        self.assertFalse(mask.any());self.assertFalse(stats['matched'])
        self.assertTrue(np.array_equal(result,a))
        a=repeated_fixture();a[490:920,290:910]=255
        result,mask,stats=patterns.repeated_diagonal(Image.fromarray(a))
        self.assertFalse(mask.any())

    def test_missing_assets_cancellation_and_packaging(self):
        image=Image.fromarray(gray_fixture())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'asset is missing'):
                patterns.unofficial_copy(image,Path(directory))
        with self.assertRaises(InterruptedError):patterns.unofficial_copy(image,ASSETS,lambda:True)
        with self.assertRaises(InterruptedError):patterns.repeated_diagonal(Image.fromarray(repeated_fixture()),lambda:True)
        spec=(ASSETS.parent/'ClearPage.spec').read_text()
        for height in patterns.GRAY_LAYOUTS:self.assertIn(f'assets/unofficial-copy-{height}.npz',spec)

    def test_application_preview_batch_selective_export_and_repair_guard(self):
        client=app.app.test_client()
        with client.get('/'):pass
        headers={'X-ClearPage':'local'}
        with tempfile.TemporaryDirectory() as directory:
            for method,a in [('huntington_background',huntington_fixture()),('unofficial_copy',gray_fixture()),('repeated_diagonal',repeated_fixture())]:
                source=Path(directory)/(method+'.pdf')
                h,w=a.shape[:2]
                canvas=Canvas(str(source),pagesize=(w/2,h/2))
                for _ in range(2):
                    canvas.drawImage(ImageReader(Image.fromarray(a)),0,0,w/2,h/2);canvas.showPage()
                canvas.save();original_bytes=source.read_bytes()
                response=client.post('/api/upload',data=original_bytes,headers=headers)
                self.assertEqual(response.status_code,200,response.json)
                key=response.json['id'];job=app.JOBS[key]
                try:
                    with patch.object(app, 'local_ocr', return_value={'lines': []}):
                        response=client.post('/api/preview/'+key,json=dict(method=method,page=0),headers=headers)
                    self.assertEqual(response.status_code,200,response.json)
                    self.assertTrue(response.json['matched'])
                    options=app.settings(dict(method=method,mode='lama',auto_restore=True,scan_inversion=True,fix_negative=True,use_reviewed=True))
                    self.assertEqual(options['auto_restore'], method == 'huntington_background');self.assertFalse(options['scan_inversion'])
                    self.assertFalse(options['fix_negative']);self.assertFalse(options['use_reviewed'])
                    options['auto_restore'] = False
                    job.update(options=options,results=[],cancel=False)
                    with patch.object(app, 'local_ocr', return_value={'lines': []}):
                        app.batch(job,options)
                    self.assertEqual(job['state'],'review',job['message'])
                    self.assertTrue(all(r['uncertain'] for r in job['results']))
                    app.export_worker(job,[0])
                    self.assertEqual(job['state'],'done',job['message'])
                    for i in range(2):
                        expected=Image.open(app.image_path(job,0,True)) if i==0 else engine.render_page(source,1)
                        self.assertTrue(np.array_equal(engine.render_page(job['dir']/'cleaned.pdf',i),expected))
                    if method == 'huntington_background':
                        self.assertEqual(app.repair_job(key, {'page': 0})[1], 0)
                    else:
                        with self.assertRaises(ValueError):app.repair_job(key,{})
                    self.assertEqual(source.read_bytes(),original_bytes)
                finally:
                    job['state']='ready';client.post('/api/clear/'+key,json={},headers=headers)


if __name__=='__main__':unittest.main()
