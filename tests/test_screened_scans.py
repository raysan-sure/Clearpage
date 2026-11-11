"""Native screen detection, source preservation and real application routing."""
from pathlib import Path
import tempfile
import unittest
import hashlib
from unittest.mock import patch
import cv2
import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
import app
from clearpage.pdf import engine
from clearpage.presets import screened_scans as screen


def example():
    ink = np.zeros(screen.SHAPE, 'uint8')
    for y in range(570, 1400, 45):
        cv2.putText(ink, 'Original document words and signatures', (270, y), cv2.FONT_HERSHEY_SIMPLEX, .8, 1, 2)
    ink[85:88, 99:201] = 1
    ink = ink.astype(bool)
    profile = screen.profile()
    y, x = np.indices(screen.SHAPE)
    phase = np.array([[0,0,0,0,1,0],[0,0,0,1,0,0],[1,0,0,0,1,1],
                      [0,1,0,0,0,0],[1,0,0,0,0,0],[0,1,1,1,0,0]], bool)
    damaged = ink.copy()
    selected = profile['scope']
    damaged[selected] = ((ink & phase[y%6, x%6]) | profile['mask'])[selected]
    return ink, damaged


def fixture(path, scans, tiles=2):
    writer = PdfWriter()
    writer.add_metadata({'/Title': 'Native tiled scan test'})
    for scan in scans:
        page = writer.add_blank_page(612, 792)
        objects = DictionaryObject()
        commands = []
        for i, band in enumerate(np.vsplit(scan, tiles)):
            image = DecodedStreamObject()
            image.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                          NameObject('/Width'): NumberObject(band.shape[1]), NameObject('/Height'): NumberObject(band.shape[0]),
                          NameObject('/BitsPerComponent'): NumberObject(8), NameObject('/ColorSpace'): NameObject('/DeviceGray')})
            image.set_data(((~band).astype('uint8')*255).tobytes())
            name = NameObject(f'/Scan{i}')
            objects[name] = writer._add_object(image.flate_encode())
            commands.append(f'q 580 0 0 {760/tiles} 16 {16+(tiles-i-1)*760/tiles} cm {name} Do Q')
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): objects})
        content = DecodedStreamObject(); content.set_data('\n'.join(commands).encode())
        page[NameObject('/Contents')] = writer._add_object(content)
    with path.open('wb') as output:
        writer.write(output)


class ScreenedScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = app.app.test_client()
        self.client.get('/').close()
        self.headers = {'X-ClearPage': 'local', 'X-Filename': 'screen.pdf'}
        self.keys = []

    def tearDown(self):
        for key in self.keys:
            app.JOBS[key]['state'] = 'ready'
            self.client.post('/api/clear/'+key, json={}, headers=self.headers).close()
        self.tmp.cleanup()

    def test_pattern_cleanup_preserves_observed_ink_and_clears_blank_paper(self):
        ink, scan = example()
        before = scan.copy()
        cleaned, details = screen.clean_pixels(scan)
        profile = screen.profile()
        self.assertTrue(details['matched'])
        self.assertTrue(details['uncertain'])
        self.assertFalse(details['ai'])
        self.assertTrue(details['stroke_reconstruction'])
        self.assertFalse(details.get('reconstruction_unverified', False))
        self.assertGreater(details['reconstructed'], 100)
        self.assertEqual(details['reviewed_word_repairs'], 0)
        self.assertTrue(np.array_equal(scan, before))
        self.assertTrue(np.array_equal(cleaned[~profile['scope']], scan[~profile['scope']]))
        self.assertFalse(np.any(scan & ~profile['mask'] & ~cleaned))
        blank = cv2.dilate(ink.astype('uint8'), np.ones((15, 15), 'uint8')) == 0
        self.assertFalse(np.any(cleaned & profile['scope'] & blank))

    def test_clean_shifted_and_wrong_screen_are_rejected(self):
        ink, scan = example()
        self.assertFalse(screen.matches(ink))
        self.assertFalse(screen.matches(np.roll(scan, 1, axis=0)))
        wrong = scan.copy(); wrong[screen.profile()['forbidden']] = True
        self.assertFalse(screen.matches(wrong))
        self.assertFalse(screen.matches(np.zeros((200, 200), bool)))
        out, details = screen.clean_pixels(ink)
        self.assertFalse(details['matched'])
        self.assertTrue(np.array_equal(out, ink))

    def test_reviewed_patch_is_bound_to_exact_source_and_cannot_remove_ink(self):
        _, scan = example()
        baseline, _ = screen.clean_pixels(scan)
        data = dict(screen.profile())
        y, x = np.argwhere(data['scope'] & ~baseline & ~scan)[0]
        data.update(fingerprint=hashlib.sha256(scan.tobytes()).hexdigest(),
                    boxes=[[int(x), int(y), int(x+1), int(y+1)]], patches=[np.ones((1, 1), bool)])
        with patch.object(screen, 'profile', return_value=data):
            changed, stats = screen.clean_pixels(scan)
            self.assertTrue(changed[y, x]); self.assertEqual(stats['reviewed_word_repairs'], 1)
            other = scan.copy(); other[20, 20] = ~other[20, 20]
            changed, stats = screen.clean_pixels(other)
            self.assertFalse(changed[y, x]); self.assertEqual(stats['reviewed_word_repairs'], 0)
            y, x = np.argwhere(scan & ~data['mask'] & data['scope'])[0]
            data.update(boxes=[[int(x), int(y), int(x+1), int(y+1)]], patches=[np.zeros((1, 1), bool)])
            with self.assertRaisesRegex(ValueError, 'ink-preservation'):
                screen.clean_pixels(scan)

    def test_native_layout_decode_and_unsupported_grayscale(self):
        _, scan = example(); source = self.root/'input.pdf'
        for tiles in (1, 2):
            fixture(source, [scan], tiles)
            p = PdfReader(source).pages[0]; bands = screen.scan_objects(p)
            self.assertTrue(np.array_equal(screen.native_pixels(p, bands), scan))
        p = PdfReader(source).pages[0]
        obj = screen.scan_objects(p)[0][1]
        with patch.object(type(p.images), '__getitem__') as images:
            images.return_value.image = Image.new('L', (1728, 1080), 211)
            self.assertIsNone(screen.native_pixels(p, screen.scan_objects(p)))
        obj[NameObject('/Width')] = NumberObject(1729)
        self.assertIsNone(screen.scan_objects(p))

    def test_cancellation_never_commits_partial_output(self):
        _, scan = example(); source = self.root/'input.pdf'; fixture(source, [scan])
        with self.assertRaises(InterruptedError):
            screen.prepare(source, 0, self.root, lambda: True)
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 10
        with self.assertRaises(InterruptedError):
            screen.prepare(source, 0, self.root, cancelled)
        self.assertFalse(list(self.root.glob('*.part')))
        self.assertFalse(list(self.root.glob('*'+screen.CACHE+'*')))

    def test_upload_suggest_preview_batch_and_lossless_selected_export(self):
        ink, scan = example(); source = self.root/'input.pdf'; fixture(source, [scan, ink])
        source_bytes = source.read_bytes()
        response = self.client.post('/api/upload', data=source_bytes, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        key = response.json['id']; self.keys.append(key); job = app.JOBS[key]
        suggestions = self.client.get('/api/suggestions/'+key).json
        self.assertEqual(suggestions['items'][0]['method'], 'screened_unofficial')
        self.assertEqual(suggestions['items'][0]['pages'], [1])
        self.assertEqual(job['results'], [])
        for method in ('screened_unofficial', 'dotted_document'):
            preview = self.client.post('/api/preview/'+key, json={'method': method, 'page': 0}, headers=self.headers)
            self.assertEqual(preview.status_code, 200, preview.json)
            self.assertTrue(preview.json['native_screened'])
            self.assertTrue(preview.json['matched'])
            options = app.settings(dict(method=method, auto_restore=True, fix_negative=True))
            self.assertFalse(options['auto_restore']); self.assertFalse(options['fix_negative'])
            job.update(options=options, results=[], cancel=False)
            app.batch(job, options)
            self.assertEqual(job['state'], 'review', job['message'])
            self.assertTrue(job['results'][0]['uncertain'])
            self.assertFalse(job['results'][1]['matched'])
            app.export_worker(job, [0])
            self.assertEqual(job['state'], 'done', job['message'])
            output = job['dir']/'cleaned.pdf'; result = PdfReader(output); original = PdfReader(source)
            self.assertEqual(len(result.pages), 2)
            self.assertEqual(result.metadata.title, original.metadata.title)
            for i, (p, q) in enumerate(zip(original.pages, result.pages)):
                expected = screen.clean_pixels(scan)[0] if i == 0 else ink
                self.assertTrue(np.array_equal(screen.native_pixels(q, screen.scan_objects(q)), expected))
                self.assertEqual(p.mediabox, q.mediabox)
                self.assertEqual(p.get_contents().get_data(), q.get_contents().get_data())
            with Image.open(app.image_path(job, 0, True)) as preview_image:
                self.assertTrue(np.array_equal(engine.render_page(output, 0), preview_image))
            with self.assertRaises(ValueError): app.repair_job(key, {})
        self.assertEqual(source.read_bytes(), source_bytes)
        response = self.client.get('/static/type-screened-unofficial.svg')
        self.assertEqual(response.status_code, 200); response.close()


if __name__ == '__main__':
    unittest.main()
