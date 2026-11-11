"""Shifted gray patterns, false matches, and opaque-mask suggestion regression."""
from io import BytesIO
from clearpage.paths import RESOURCE_ROOT
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from PIL import Image,ImageDraw
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject,NumberObject
import app
from clearpage.presets import aligned_gray, gray_scans, scan_patterns

ASSETS=RESOURCE_ROOT/'assets'

def fixture():
    with np.load(ASSETS/'unofficial-copy-2024.npz') as d:base=d['template']
    w,h=1549,2183
    t=cv2.resize(base,(w,h))
    warp=np.float32([[.96,-.033,68],[.037,1.03,-67]])
    template=cv2.warpAffine(t,warp,(w,h),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderValue=(255,255,255))
    truth=np.full((h,w,3),255,'uint8')
    for y in range(400,1800,70):truth[y:y+4,200:1350]=25
    a=np.rint(truth.astype('float32')*template/255).astype('uint8')
    return a


class AlignedGrayTests(unittest.TestCase):
    def test_shifted_pattern_and_preservation(self):
        a=fixture();result,mask,details=scan_patterns.unofficial_copy(Image.fromarray(a),ASSETS)
        self.assertEqual(details['alignment_version'],1)
        self.assertGreater(details['changed'],10000)
        np.testing.assert_array_equal(result[a.min(2)<=75],a[a.min(2)<=75])
        np.testing.assert_array_equal(result[~mask],a[~mask])
        self.assertFalse(details['ai']);self.assertTrue(details['uncertain'])

    def test_reject_clean_other_text_and_color(self):
        white=Image.new('RGB',(700,1000),'white')
        text=white.copy();draw=ImageDraw.Draw(text)
        for y in range(50,950,40):draw.text((40,y),'Different gray document text 12345',fill=(150,150,150),font_size=22)
        color=Image.fromarray(fixture());color.paste((200,0,0),(0,0,400,400))
        for image in (white,text,color):self.assertIsNone(aligned_gray.clean(image,ASSETS))
        with self.assertRaises(InterruptedError):aligned_gray.clean(white,ASSETS,lambda:True)
        with patch.object(cv2,'findTransformECC',side_effect=cv2.error('No convergence')):
            self.assertIsNone(aligned_gray.clean(Image.fromarray(fixture()),ASSETS))

    def test_opaque_alpha_suggestion_and_preview(self):
        a=fixture();buf=BytesIO();canvas=Canvas(buf,pagesize=(371.76,523.92))
        canvas.drawImage(ImageReader(Image.fromarray(a)),0,0,371.76,523.92);canvas.save()
        writer=PdfWriter();writer.clone_document_from_reader(PdfReader(buf))
        obj=next(iter(writer.pages[0]['/Resources']['/XObject'].values())).get_object()
        mask=DecodedStreamObject();mask.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),NameObject('/Width'):NumberObject(a.shape[1]),NameObject('/Height'):NumberObject(a.shape[0]),NameObject('/BitsPerComponent'):NumberObject(8),NameObject('/ColorSpace'):NameObject('/DeviceGray')})
        mask.set_data(bytes([255])*a.shape[0]*a.shape[1]);obj[NameObject('/SMask')]=writer._add_object(mask.flate_encode())
        data=BytesIO();writer.write(data)
        client=app.app.test_client();headers={'X-ClearPage':'local'}
        response=client.post('/api/upload',data=data.getvalue(),headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        key=response.json['id']
        try:
            found=client.get('/api/suggestions/'+key)
            self.assertEqual(found.status_code,200,found.json)
            self.assertEqual(found.json['items'][0]['method'],'unofficial_copy')
            preview=client.post('/api/preview/'+key,json={'method':'unofficial_copy','page':0},headers=headers)
            self.assertEqual(preview.status_code,200,preview.json)
            self.assertEqual(preview.json['alignment_version'],1)
            self.assertTrue(preview.json['native_gray'])
        finally:
            app.JOBS[key]['state']='ready';client.post('/api/clear/'+key,json={},headers=headers)


if __name__=='__main__':unittest.main()
