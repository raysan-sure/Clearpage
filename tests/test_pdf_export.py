"""Rendered regression for gray watermark outlines reintroduced on PDF export."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

from clearpage.pdf import engine


class SparseExportTests(unittest.TestCase):
    def test_correction_has_no_resampling_outline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = Image.new('RGB', (300, 333), 'white')
            draw = ImageDraw.Draw(before)
            draw.ellipse((30, 35, 155, 255), fill=(150, 150, 150))
            draw.rectangle((195, 20, 215, 310), fill='black')
            after = Image.new('RGB', before.size, 'white')
            ImageDraw.Draw(after).rectangle((195, 20, 215, 310), fill='black')
            before.save(root/'before.png'); after.save(root/'after.png')

            def pdf(path, image):
                canvas = Canvas(str(path), pagesize=(143, 161))
                canvas.drawImage(ImageReader(image), 0, 0, width=143, height=161)
                canvas.showPage(); canvas.save()

            pdf(root/'source.pdf', before)
            pdf(root/'reference.pdf', after)
            engine.write_pdf(root/'source.pdf', root/'clean.pdf',
                             {0: (root/'before.png', root/'after.png')})
            actual = np.array(engine.render_page(root/'clean.pdf', 0))
            expected = np.array(engine.render_page(root/'reference.pdf', 0))
            # The entire removed ellipse and its edge must render white, even
            # though the display grid does not align to the image's pixels.
            self.assertGreaterEqual(int(actual[:, :175].min()), 253)
            # The unrelated black content is still pixel-identical.
            np.testing.assert_array_equal(actual[:, 180:], expected[:, 180:])


if __name__ == '__main__':
    unittest.main()
