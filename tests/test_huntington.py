"""Preservation, convergence and confirmed repair regressions for Huntington."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
from clearpage.presets import huntington
import app


class HuntingtonTests(unittest.TestCase):
    def test_passes_stop_without_eroding_text_or_prior_edits(self):
        source = np.full((120, 400, 3), 255, 'uint8')
        scope = np.zeros((120, 400), bool); scope[15:100, 190:215] = True
        source[scope] = 110
        source[45:51, 30:360] = 0  # numerical/text stroke through the watermark
        protect = np.zeros_like(scope); protect[40:57] = True
        current = source.copy(); current[65:75, 195:205] = 23  # accepted repair
        locked = np.zeros_like(scope); locked[65:75, 195:205] = True
        after, detail = huntington.refine(source, current, scope, protect, locked=locked)
        self.assertTrue(detail['converged'])
        self.assertEqual(detail['stop_reason'], 'text_review_required')
        np.testing.assert_array_equal(after[~scope | protect | locked], current[~scope | protect | locked])
        again, second = huntington.refine(source, after, scope, protect, locked=locked)
        np.testing.assert_array_equal(again, after)
        self.assertEqual(sum(r['changed_pixels'] for r in second['steps']), 0)
        with self.assertRaises(InterruptedError):
            huntington.refine(source, current, scope, protect, cancelled=lambda: True)

    def test_ocr_geometry_clears_gaps_but_not_the_row(self):
        source = np.full((130, 400, 3), 255, 'uint8')
        scope = np.zeros((130, 400), bool); scope[30:85, 180:200] = True
        source[scope] = 80
        words = [dict(text='123' if x==30 else 'word', x=x, y=50, w=40, h=15) for x in (30,90,250,310)]
        for w in words: source[50:65, w['x']:w['x']+40] = 0
        ocr = {'lines':[{'words':words}]}
        selected, count = huntington.line_gaps(source, scope, ocr)
        self.assertEqual(count, 1)
        self.assertTrue(selected.any())
        self.assertFalse(selected[48:67].any())
        self.assertFalse(selected[~scope].any())
        self.assertFalse(huntington.line_gaps(source, scope, {'lines':[]})[0].any())

    def test_confirmed_patch_survives_processing_and_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = np.full((160, 240, 3), 255, 'uint8')
            source[20:140, 110:125] = 100
            Image.fromarray(source).save(root/'0-before.png')
            path = root/'0-before.png'
            box = [105,60,135,80]
            accepted = Image.new('RGB', (30,20), (40,40,40))
            job = dict(dir=root, count=1, cancel=False, repairs={0:[dict(box=box, patch=accepted, text='confirmed')]},
                       automatic={}, options=app.settings(dict(method='huntington_background')))
            cleanup = source.copy(); cleanup[20:140,110:125] = 255
            details = dict(matched=True, changed=1800, scan_pattern=True, confidence=.9,
                           pattern_note='Review', uncertain=True, protected=0)
            with patch.object(app, 'original', return_value=path), patch.object(app, 'source_ocr_cache', return_value=root/'ocr.json'), \
                 patch.object(app, 'local_ocr', return_value={'lines':[]}), \
                 patch.object(app.scan_patterns, 'huntington_background', return_value=(cleanup, np.any(cleanup!=source,2), details)):
                result = app.process_page(job, 0, job['options'])
                actual = np.array(Image.open(app.image_path(job,0,True)))
                self.assertEqual(result['repairs'], 1)
                np.testing.assert_array_equal(actual[60:80,105:135], np.array(accepted))
                job['repairs'] = {}
                app.process_page(job, 0, job['options'])
                np.testing.assert_array_equal(np.array(Image.open(app.image_path(job,0,True))), cleanup)

    def test_disagreement_and_digits_never_create_donor_repairs(self):
        source = np.full((80,300,3),255,'uint8');source[30:45,120:150]=50
        mask = np.zeros((80,300),bool);mask[30:45,120:150]=True
        original={'lines':[{'words':[dict(text='12345',x=100,y=30,w=60,h=15)]}]}
        other={'lines':[{'words':[dict(text='l2345',x=100,y=30,w=60,h=15)]}]}
        result, repairs=huntington.exact_donors(source,source,mask,original,other,{})
        self.assertEqual(repairs,[])
        np.testing.assert_array_equal(result,source)


if __name__ == '__main__':
    unittest.main()
