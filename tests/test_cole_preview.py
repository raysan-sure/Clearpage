"""Cole preview detection, mixed export and protection of unrelated print."""
from pathlib import Path
import tempfile
import unittest
import zlib

import numpy as np
import pikepdf

from clearpage.presets import cole_preview as cole


class ColePreviewTests(unittest.TestCase):
    def test_labels_and_cross_are_both_required(self):
        stamp = cole.stamp()
        self.assertTrue(cole.matches(stamp))
        self.assertFalse(cole.matches(np.full_like(stamp,255)))
        labels_only = np.full_like(stamp,255)
        for box in cole.LABELS:
            cole.crop(labels_only,box)[:] = cole.crop(stamp,box)
        self.assertFalse(cole.matches(labels_only))
        self.assertFalse(cole.matches(np.roll(stamp,12,axis=1)))

    def test_other_deed_and_unselected_page(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, target = directory/'source.pdf', directory/'result.pdf'
            gray = cole.stamp().copy()
            gray[635:640,55:85] = 0
            with pikepdf.new() as doc:
                obj = doc.make_stream(zlib.compress(gray.tobytes()))
                obj.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                    Filter=pikepdf.Name.FlateDecode, ColorSpace=pikepdf.Name.DeviceGray,
                    Width=779,Height=1019,BitsPerComponent=8))
                for _ in range(2):
                    page = doc.add_blank_page()
                    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=obj))
                    page.Contents = doc.make_stream(b'q /Scan Do Q')
                doc.save(source)
            _, result = cole.prepare(source,1,directory)
            self.assertTrue(result['matched'])
            self.assertEqual(result['word_repairs'],0)
            cole.write_clean(source,target,[1],directory)
            with pikepdf.open(target) as doc:
                self.assertEqual(doc.pages[0].Resources.XObject.Scan.read_bytes(),gray.tobytes())
                clean = np.asarray(pikepdf.PdfImage(doc.pages[1].Resources.XObject.Scan).as_pil_image())
                self.assertTrue(np.array_equal(clean[cole.stamp()==255],gray[cole.stamp()==255]))
                self.assertFalse(cole.matches(clean))
                self.assertEqual(doc.pages[1].Contents.read_bytes(),b'q /Scan Do Q')
