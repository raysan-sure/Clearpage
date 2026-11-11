"""Synthetic regression coverage for terminal Un-Official PDF text overlays."""
from pathlib import Path
import tempfile
import unittest
from pypdf import PdfWriter
from pypdf.generic import (NameObject as N, DictionaryObject as D,
                           DecodedStreamObject, FloatObject, NumberObject)
from clearpage.pdf import pdf_overlays as overlay
from clearpage.pdf import engine

# Public font character mapping only; no customer page or scan is included.
CMAP = b'/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo\n<< /Registry (TTX+0)\n/Ordering (T42UV)\n/Supplement 0\n>> def\n/CMapName /TTX+0 def\n/CMapType 2 def\n1 begincodespacerange\n<0000><FFFF>\nendcodespacerange\n9 beginbfrange\n<0010><0010><002d>\n<0032><0032><004f>\n<0038><0038><0055>\n<0044><0044><0061>\n<0046><0046><0063>\n<0049><0049><0066>\n<004c><004c><0069>\n<004f><004f><006c>\n<0051><0051><006e>\nendbfrange\nendcmap\nCMapName currentdict /CMap defineresource pop\nend end\n'
SUFFIX = (b'/GS1 gs BT .75294 .75294 .75294 rg /F1 87.43 Tf '
          b'.70711 -.70711 .70711 .70711 141.09 560.91 Tm '
          b'<003800510010003200490049004c0046004c0044004f> Tj '
          b'1 0 0 1 0 0 Tm ET')


def sample(path, suffix=SUFFIX, cmap=CMAP, opacity=.70588, prefix=b'q /Scan Do Q '):
    w = PdfWriter()
    image = DecodedStreamObject(); image.set_data(b'\x00\xff\x00\xff')
    image.update({N('/Type'): N('/XObject'), N('/Subtype'): N('/Image'),
                  N('/Width'): NumberObject(2), N('/Height'): NumberObject(2),
                  N('/BitsPerComponent'): NumberObject(8), N('/ColorSpace'): N('/DeviceGray')})
    mapping = DecodedStreamObject(); mapping.set_data(cmap)
    font = D({N('/Subtype'): N('/Type0'), N('/Encoding'): N('/Identity-H'),
              N('/BaseFont'): N('/ABCDEF+OpenSans-Bold'), N('/ToUnicode'): w._add_object(mapping)})
    resources = D({N('/XObject'): D({N('/Scan'): w._add_object(image)}),
                   N('/Font'): D({N('/F1'): w._add_object(font)}),
                   N('/ExtGState'): D({N('/GS1'): D({N('/ca'): FloatObject(opacity)})})})
    for _ in range(2):
        p = w.add_blank_page(612, 792)
        p[N('/Resources')] = resources
        stream = DecodedStreamObject(); stream.set_data(prefix + suffix)
        p[N('/Contents')] = w._add_object(stream)
    w.write(path)


class UnofficialOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name)/'source.pdf'
        self.target = Path(self.tmp.name)/'cleaned.pdf'

    def tearDown(self): self.tmp.cleanup()

    def test_document_and_large_map_sizes(self):
        for size in (b'87.43', b'518.43'):
            sample(self.source, SUFFIX.replace(b'87.43', size))
            self.assertEqual([p['overlays'] for p in overlay.inspect(self.source)], [1, 1])

    def test_selected_page_preserves_scan_and_other_page(self):
        sample(self.source)
        overlay.write_clean(self.source, self.target, [0])
        self.assertEqual([p['overlays'] for p in overlay.inspect(self.target)], [0, 1])
        a, b = overlay.read_pdf(self.source), overlay.read_pdf(self.target)
        self.assertEqual(a.pages[1].get_contents().get_data(), b.pages[1].get_contents().get_data())
        for old, new in zip(a.pages, b.pages):
            self.assertEqual(old.mediabox, new.mediabox)
            self.assertEqual(old['/Resources']['/XObject']['/Scan']._data,
                             new['/Resources']['/XObject']['/Scan']._data)

    def test_large_map_upload_admission_keeps_limits(self):
        for width, suffix, allowed in [(3629, SUFFIX, True), (3629, b'', False),
                                        (20001, SUFFIX, False)]:
            sample(self.source, suffix)
            w = PdfWriter(); w.clone_document_from_reader(overlay.read_pdf(self.source))
            for page in w.pages: page.mediabox.upper_right = (width, 2815)
            w.write(self.source)
            with self.subTest(width=width, allowed=allowed):
                if allowed:
                    self.assertEqual(engine.inspect_pdf(self.source)[0], 2)
                else:
                    with self.assertRaises(ValueError): engine.inspect_pdf(self.source)

    def test_rejects_unrecognized_or_unsafe_blocks(self):
        variants = [
            {'suffix': SUFFIX.replace(b'0038', b'0039')},
            {'suffix': SUFFIX.replace(b'.75294', b'.25')},
            {'suffix': SUFFIX.replace(b'.70711 -.70711 .70711 .70711', b'1 0 0 1')},
            {'suffix': SUFFIX + b' 0 0 10 10 re f'},
            {'suffix': SUFFIX.replace(b'87.43', b'12')},
            {'cmap': CMAP.replace(b'<0055>', b'<0056>')},
            {'opacity': 1},
            {'prefix': b'q Q '},
            {'prefix': b'q /Scan Do '},
            {'prefix': b'q /Scan Do Q BT /F1 12 Tf (ordinary words) Tj ET '},
        ]
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                sample(self.source, **kwargs)
                self.assertFalse(any(p['overlays'] for p in overlay.inspect(self.source)))


if __name__ == '__main__': unittest.main(verbosity=2)
