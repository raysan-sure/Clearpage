"""Cole preview detection, mixed export and protection of unrelated print."""
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
import zlib

import numpy as np
import pikepdf
from PIL import Image

from clearpage.presets import cole_preview as cole


class ColePreviewTests(unittest.TestCase):
    def test_review_preserves_native_pixels_and_composites_other_page_marks(self):
        with tempfile.TemporaryDirectory() as temp, pikepdf.new() as doc:
            path = Path(temp) / 'review.pdf'
            # Alternating strokes disappear in the old 327-pixel-wide preview.
            gray = np.full((899, 681), 255, np.uint8)
            gray[200:300, 100:200:2] = 0
            obj = doc.make_stream(zlib.compress(gray.tobytes()))
            obj.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                Filter=pikepdf.Name.FlateDecode, ColorSpace=pikepdf.Name.DeviceGray,
                Width=681, Height=899, BitsPerComponent=8))
            page = doc.add_blank_page(page_size=(163.44, 215.76))
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=obj))
            draw = b'q 163.44 0 0 215.76 0 0 cm /Scan Do Q'
            page.Contents = doc.make_stream(draw)
            doc.save(path)
            np.testing.assert_array_equal(cole.display_image(path, 0), gray)
            page.Contents = doc.make_stream(draw + b' 0 g 10 10 10 10 re f')
            doc.save(path)
            composed = cole.display_image(path, 0).convert('L')
            self.assertEqual(composed.getpixel((60, 840)), 0)

    def test_jpeg_layouts_and_mask(self):
        for width, height in sorted(cole.JPEG_SIZES):
            with self.subTest(height=height), pikepdf.new() as doc:
                stamp = cole.stamp((width, height))
                labels_only = np.full_like(stamp, 255)
                for box in cole.JPEG_LABELS:
                    cole.crop(labels_only, box)[:] = cole.crop(stamp, box)
                self.assertFalse(cole.matches(labels_only))
                self.assertFalse(cole.matches(np.roll(stamp, 5, axis=1)))
                pixels = stamp.copy()
                pixels[110:150, 410:412] = 90
                encoded = BytesIO()
                Image.fromarray(pixels).convert('RGB').save(encoded, 'JPEG', quality=94)
                obj = doc.make_stream(encoded.getvalue())
                obj.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                    Filter=pikepdf.Name.DCTDecode, ColorSpace=pikepdf.Name.DeviceRGB,
                    Width=width, Height=height, BitsPerComponent=8))
                page = doc.add_blank_page()
                page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=obj))
                page.Contents = doc.make_stream(b'q /Scan Do Q')
                alpha = doc.make_stream(b'\xff' * pixels.size)
                alpha.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                    ColorSpace=pikepdf.Name.DeviceGray, Width=width, Height=height, BitsPerComponent=8))
                obj.SMask = alpha
                _, gray = cole.scan(page)
                result = cole.restore(gray)
                self.assertFalse(cole.matches(result))
                self.assertTrue(np.array_equal(result[105:155, 405:415], gray[105:155, 405:415]))
                self.assertLess(np.mean(255 - result[29:50, 100:580]), 3)
                alpha.write(b'\x00' + b'\xff' * (pixels.size - 1))
                self.assertIsNone(cole.scan(page))
                del obj['/SMask']
                rgb = Image.fromarray(pixels).convert('RGB')
                rgb.paste('red', (400, 100, 430, 130))
                encoded = BytesIO()
                rgb.save(encoded, 'JPEG')
                obj.write(encoded.getvalue(), filter=pikepdf.Name.DCTDecode)
                self.assertIsNone(cole.scan(page))

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
