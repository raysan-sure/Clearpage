from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zlib

import cv2 as cv
import numpy as np
import pikepdf
from clearpage.presets import hocking_outline as hocking


def sample():
    stamp = hocking.profile()
    ink = np.zeros_like(stamp)
    for pos, text in [((480, 1460), 'within 30 days'), ((760, 1220), 'R. Smith')]:
        cv.putText(ink, text, pos, cv.FONT_HERSHEY_SIMPLEX, 1.2, 255, 3)
    # Like the county scan: the document ink has priority over the outline.
    paper = cv.dilate(ink, np.ones((5, 5), np.uint8)) > 0
    marked = stamp.copy()
    marked[paper] = 255 - ink[paper]
    return marked, ink > 0


def write_shared_pages(path, scan):
    with pikepdf.new() as doc:
        obj = doc.make_stream(zlib.compress(cv.cvtColor(scan, cv.COLOR_GRAY2RGB).tobytes()))
        obj.Type = pikepdf.Name.XObject
        obj.Subtype = pikepdf.Name.Image
        obj.Filter = pikepdf.Name.FlateDecode
        obj.ColorSpace = pikepdf.Name.DeviceRGB
        obj.Width, obj.Height, obj.BitsPerComponent = scan.shape[1], scan.shape[0], 8
        resources = doc.make_indirect(pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=obj)))
        for _ in range(2):
            page = doc.add_blank_page(page_size=(612, 792))
            page.Resources = resources
            page.Contents = doc.make_stream(b'q 612 0 0 792 0 0 cm /Scan Do Q')
        doc.save(path)


class HockingOutlineTests(unittest.TestCase):
    def test_peer_confirms_a_clipped_arc_without_erasing_the_word(self):
        paper = np.full((112, 144), 255, np.uint8)
        witness = paper.copy()
        cv.ellipse(witness, (70, 60), (20, 14), 0, 0, 360, 20, 1, cv.LINE_AA)
        # The word is printed over opaque paper, which interrupts the ring.
        scan = witness.copy()
        scan[:, 57:85] = 255
        cv.putText(scan, 'F', (61, 75), cv.FONT_HERSHEY_SIMPLEX, .9, 0, 2, cv.LINE_AA)
        unchanged = hocking.clear_clipped_arcs(scan, scan.copy(), witness, [scan.copy()])
        np.testing.assert_array_equal(unchanged, scan)
        result = hocking.clear_clipped_arcs(scan, scan.copy(), witness, [witness])
        np.testing.assert_array_equal(result[:, 57:85], scan[:, 57:85])
        self.assertGreater(np.count_nonzero((scan != result) & (witness < 175)), 12)

    def test_preserves_foreground_at_crossings(self):
        scan, ink = sample()
        cleaned, details = hocking.clean_pixels(scan)
        self.assertTrue(details['matched'])
        self.assertTrue(np.array_equal(cleaned[ink], scan[ink]), 'lost original foreground ink')
        self.assertLess(np.count_nonzero((cleaned < 180) & ~ink),
                        np.count_nonzero((scan < 180) & ~ink) * .01)

    def test_unrelated_partial_and_clean_pages_are_kept(self):
        blank = np.full_like(hocking.profile(), 255)
        partial = blank.copy()
        partial[:, :550] = hocking.profile()[:, :550]
        unrelated = blank.copy()
        cv.line(unrelated, (200, 1855), (1460, 615), 0, 3)
        for img in (blank, partial, unrelated, blank[:500]):
            out, info = hocking.clean_pixels(img)
            self.assertFalse(info['matched'])
            self.assertIs(out, img)

    def test_selected_page_export_and_cancellation(self):
        scan, _ = sample()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root/'input.pdf', root/'cleaned.pdf'
            write_shared_pages(source, scan)
            cached, detail = hocking.prepare(source, 0, root)
            self.assertTrue(detail['matched'])
            hocking.write_clean(source, target, [0], root)
            with pikepdf.open(cached) as preview, pikepdf.open(target) as out:
                self.assertEqual(len(out.pages), 2)
                pixels = lambda pg: hocking.native(hocking.image_entry(pg))
                self.assertTrue(np.array_equal(pixels(preview.pages[0]), pixels(out.pages[0])))
                self.assertTrue(np.array_equal(scan, pixels(out.pages[1])))
            before = target.read_bytes()
            with self.assertRaises(InterruptedError):
                hocking.write_clean(source, target, [0], root, cancelled=lambda: True)
            self.assertEqual(before, target.read_bytes())


if __name__ == '__main__':
    unittest.main()
