from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import clean_repeated
import app


class OfflineScriptTests(unittest.TestCase):
    def test_original_is_unchanged_and_existing_outputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); source=folder/'source.png'; output=folder/'output.png'
            Image.new('RGB',(400,600),'white').save(source)
            original=source.read_bytes()
            with patch.object(app.gemini_cleanup,'reconstruct',side_effect=AssertionError('Cloud called')), \
                 patch.object(app.neural_restore,'inpaint',side_effect=AssertionError('AI called')):
                report=clean_repeated.run(source,output)
            self.assertTrue(report['no_ai'])
            self.assertEqual(report['pages'][0]['changed'],0)
            with Image.open(output) as image:
                self.assertEqual(image.size,(400,600))
            self.assertEqual(source.read_bytes(),original)
            for target in (source,output,folder/'wrong.jpg'):
                with self.assertRaises(ValueError):clean_repeated.run(source,target)


if __name__=='__main__':unittest.main()
