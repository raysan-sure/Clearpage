"""Guards against editing ordinary text or similarly sized, unmarked scans."""
import unittest
import zlib

import numpy as np
import pikepdf
from pypdf.generic import ContentStream, DecodedStreamObject

import lake_outline
import pdf_overlays
import preble_resampled


class RecorderProfileTests(unittest.TestCase):
    def test_lettered_overlay_requires_the_complete_translucent_diagonal_word(self):
        resources = {'/Font': {'/F': {'/Subtype':'/TrueType','/BaseFont':'/Test+Calibri-Bold'}},
                     '/ExtGState': {'/Light': {'/ca':.35}}, '/XObject': {'/Scan':{'/Subtype':'/Image'}}}
        def ranges(word='UNOFFICIAL', alpha=.35, diagonal=True):
            resources['/ExtGState']['/Light']['/ca'] = alpha
            matrix = '82 -136 -137 -83' if diagonal else '82 0 0 82'
            letters = ' '.join(f'BT {matrix} {100+j*36} {800-j*55} Tm /F 1 Tf ({c}) Tj ET'
                               for j,c in enumerate(word))
            stream = DecodedStreamObject()
            stream.set_data(f'q /Scan Do Q q .25098 .25098 .25098 rg /Light gs {letters} Q'.encode())
            return pdf_overlays.lettered_unofficial_ranges(ContentStream(stream,None).operations,resources)
        self.assertEqual(len(ranges()),1)
        self.assertFalse(ranges(word='UNOFFICIAI'))
        self.assertFalse(ranges(alpha=1))
        self.assertFalse(ranges(diagonal=False))

    def test_blank_scan_is_not_a_preble_watermark(self):
        self.assertIsNone(preble_resampled.locate(np.full((2000,1700),255,'uint8')))

    def test_lake_removes_matching_outline_and_keeps_unmarked_page(self):
        mask = lake_outline.profiles()[0][0].astype(bool)
        original = np.zeros_like(mask)
        original[100:108, 300:750] = True
        with pikepdf.new() as doc:
            for ink in (original | mask, original):
                page = doc.add_blank_page()
                image = doc.make_stream(zlib.compress(np.packbits(~ink,axis=1).tobytes()))
                image.update(pikepdf.Dictionary(Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                    Width=ink.shape[1], Height=ink.shape[0], ColorSpace=pikepdf.Name.DeviceGray,
                    Filter=pikepdf.Name.FlateDecode, BitsPerComponent=1))
                page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=image))
                page.Contents = doc.make_stream(b'q /Scan Do Q')
            result = lake_outline.process(doc,0,None,lambda:False)
            self.assertTrue(np.array_equal(result[1],np.packbits(~original,axis=1)))
            self.assertIsNone(lake_outline.process(doc,1,None,lambda:False))
