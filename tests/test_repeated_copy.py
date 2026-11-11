from io import BytesIO
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from clearpage.presets import repeated_copy as rc


def fixture():
    a = np.full((800,600,3),255,np.uint8)
    a[250:254,20:580] = 0
    with Image.open(rc.ASSET) as image: tile = np.array(image.convert('RGB'))
    template = np.full_like(a,255)
    for y in range(20,700,130):
        for x in range(20,500,115): template[y:y+90,x:x+95] = tile
    alpha = np.clip((255-template.astype(float))/85,0,.85)
    observed = np.rint(a*(1-alpha)+170*alpha).astype(np.uint8)
    return a,observed,template


class RepeatedCopyTests(unittest.TestCase):
    def test_pdf_streams_closed_on_success_failure_and_export(self):
        _, observed, _ = fixture()
        data=BytesIO(); canvas=Canvas(data,pagesize=(600,800))
        canvas.drawImage(ImageReader(Image.fromarray(observed)),0,0,600,800);canvas.save()
        readers=[]
        real_read=rc.read_pdf
        def track(path):
            reader=real_read(path); readers.append(reader); return reader
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.pdf';source.write_bytes(data.getvalue())
            with patch.object(rc,'read_pdf',side_effect=track):
                rc.prepare(source,0,directory)
                self.assertTrue(all(r.stream.closed for r in readers))
                rc.write_clean(source,Path(directory)/'export.pdf',[0],directory)
                self.assertTrue(all(r.stream.closed for r in readers))
                self.assertTrue(all(not r.resolved_objects for r in readers))
                with tempfile.TemporaryDirectory() as failed:
                    with patch.object(rc,'clean',side_effect=MemoryError):
                        with self.assertRaises(MemoryError):rc.prepare(source,0,failed)
                self.assertTrue(all(r.stream.closed for r in readers))

    def test_recognition_preserves_unaffected_pixels(self):
        original, observed, template = fixture()
        result, changed, details = rc.clean(Image.fromarray(observed))
        self.assertTrue(details['matched']); self.assertFalse(details['ai'])
        self.assertTrue(details['uncertain']); self.assertGreater(details['copies'],15)
        # Plenty of unrelated content remains byte-for-byte unchanged.
        np.testing.assert_array_equal(result[template.min(2)==255],observed[template.min(2)==255])
        self.assertLess(np.mean(abs(result.astype(float)-original)),np.mean(abs(observed.astype(float)-original)))

    def test_reject_clean_single_glyph_and_color(self):
        for image in (Image.new('RGB',(600,800),'white'), Image.new('RGB',(600,800),'red')):
            a,changed,details=rc.clean(image)
            self.assertFalse(details['matched']);self.assertFalse(changed.any())
            np.testing.assert_array_equal(a,np.array(image))
        single=Image.new('RGB',(600,800),'white')
        with Image.open(rc.ASSET) as glyph: single.paste(glyph,(20,20))
        self.assertFalse(rc.clean(single)[2]['matched'])

    def test_cancel(self):
        with self.assertRaises(InterruptedError):
            rc.clean(Image.new('RGB',(600,800)),lambda:True)

    def test_shared_pattern_resources_leave_other_page_unchanged(self):
        a,observed,_=fixture()
        data=BytesIO();canvas=Canvas(data,pagesize=(600,800))
        canvas.drawImage(ImageReader(Image.fromarray(observed)),0,0,600,800);canvas.save()
        writer=PdfWriter();writer.add_page(PdfReader(data).pages[0])
        page=writer.pages[0]
        objects=page['/Resources']['/XObject']
        pattern=DecodedStreamObject();pattern.set_data(b'')
        pattern.update({NameObject('/PatternType'):NumberObject(1),NameObject('/PaintType'):NumberObject(1),NameObject('/Resources'):DictionaryObject({NameObject('/XObject'):objects})})
        resources=DictionaryObject({NameObject('/Pattern'):DictionaryObject({NameObject('/P'):writer._add_object(pattern)})})
        page[NameObject('/Resources')]=resources
        second=writer.add_blank_page(width=600,height=800)
        second[NameObject('/Resources')]=resources
        found=rc.scan_object(page);self.assertIsNotNone(found)
        rc.replace(writer,page,found,a)
        np.testing.assert_array_equal(np.array(rc.pixels(rc.scan_object(page))),a)
        np.testing.assert_array_equal(np.array(rc.pixels(rc.scan_object(second))),observed)


if __name__=='__main__': unittest.main()
