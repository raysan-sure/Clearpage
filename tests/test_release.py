"""Release gates must reject mismatched versions before any build or upload."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from scripts import release


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'static').mkdir()
        (self.root/'clearpage').mkdir()
        (self.root/'static/index.html').write_text('ClearPage 1.9\nClearPage 1.9', encoding='utf-8')
        (self.root/'app.py').write_text("download_name='sample_ClearPage-1.9.pdf'", encoding='utf-8')
        (self.root/'README.md').write_text('# ClearPage 1.9\n', encoding='utf-8')
        (self.root/'clearpage/app_version.py').write_text("VERSION = '1.9'\n", encoding='utf-8')
        self.addCleanup(self.tmp.cleanup)
        self.root_patch = patch.object(release, 'ROOT', self.root)
        self.root_patch.start();self.addCleanup(self.root_patch.stop)

    def test_matching_release_and_manual_build(self):
        for tag in ('v1.9', ''):
            with patch.dict(os.environ, RELEASE_TAG=tag):
                self.assertEqual(release.version(), '1.9')

    def test_wrong_or_unsafe_tag_cannot_publish(self):
        for tag in ('v1.10', '1.9', '../../bad', 'v1.9; echo invalid'):
            with patch.dict(os.environ, RELEASE_TAG=tag), self.assertRaises(ValueError):
                release.version()

    def test_inconsistent_title_and_footer_rejected(self):
        (self.root/'clearpage/app_version.py').write_text("VERSION = '1.9-beta'\n", encoding='utf-8')
        with patch.dict(os.environ, RELEASE_TAG=''), self.assertRaises(ValueError):
            release.version()

    def test_old_export_name_rejected(self):
        (self.root/'app.py').write_text("download_name='sample_ClearPage-1.8.pdf'", encoding='utf-8')
        with patch.dict(os.environ, RELEASE_TAG=''), self.assertRaises(ValueError):
            release.version()

    def test_old_readme_rejected(self):
        (self.root/'README.md').write_text('# ClearPage 1.8\n', encoding='utf-8')
        with patch.dict(os.environ, RELEASE_TAG=''), self.assertRaises(ValueError):
            release.version()

    def test_notice_collection_does_not_ship_installed_bytecode(self):
        (self.root/'vendor').mkdir()
        python = self.root/'python'
        python.mkdir()
        (python/'LICENSE.txt').write_text('Python license', encoding='utf-8')
        packages = self.root/'packages'
        packages.mkdir()
        paths = ['licenses/LICENSE.txt', 'licenses/__pycache__/cached.pyc', 'licenses/old.pyo']
        for name in paths:
            target = packages/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'build-machine-path' if name.endswith(('.pyc', '.pyo')) else b'Original license')
        names = ['pypdfium2', 'pikepdf', 'lxml', 'opencv-python-headless', 'numpy', 'pillow', 'cryptography', 'flask']
        if release.sys.platform == 'win32':
            names += ['pywebview', 'pythonnet', 'clr-loader']
        distributions = [SimpleNamespace(metadata={'Name': name}, version='1', files=paths,
                          locate_file=lambda path: packages/path, read_text=lambda name: 'Metadata') for name in names]
        distributions += [SimpleNamespace(metadata={'Name': name}) for name in ('torch', 'onnxruntime')]
        bundle = self.root/'bundle'
        bundle.mkdir()
        with patch.object(release.sys, 'base_prefix', str(python)), patch.object(release.metadata, 'distributions', return_value=distributions):
            release.collect_licenses(bundle, standard=True)
        self.assertEqual(len(list((bundle/'licenses').rglob('licenses/LICENSE.txt'))), len(names))
        self.assertFalse(list(bundle.rglob('*.pyc')))
        self.assertFalse(list(bundle.rglob('*.pyo')))
        self.assertNotIn('torch', (bundle/'DEPENDENCIES.txt').read_text())
        self.assertNotIn('onnxruntime', (bundle/'DEPENDENCIES.txt').read_text())

    def test_owner_profile_is_resolved_without_embedded_home_directory(self):
        from scripts.owner_test_build import profile_policy
        profile = Path.home()/'ClearPage-private-tests'/'profile'
        policy = profile_policy(profile)
        self.assertNotIn(str(Path.home()), policy)
        self.assertNotIn(Path.home().name, policy)
        with patch.dict(os.environ, clear=False):
            exec(policy, {})
            self.assertEqual(Path(os.environ['CLEARPAGE_DATA_DIR']), profile)


if __name__ == '__main__':
    unittest.main()
