import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import publish_release, standard_release


class AutomatedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.archive=self.root/'ClearPage-3.0-Windows-x64.zip';self.archive.write_bytes(b'verified artifact')
        (self.root/'ClearPage-Windows-x64.zip').write_bytes(self.archive.read_bytes())
        self.report=dict(version='3.0',preview=False,sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest(),checks=[dict(mode=m,locked=True) for m in ('normal','environment-override','changed-config','missing-config')])
        self.report['desktop_check']=dict(renderer='edgechromium',ui_ready=True,download_marks=True)
        self.report['updater_check']=dict(installed=True,rollback=True,resume=True,user_files_preserved=True)
        self.write_report()
        (self.root/'release-notes.md').write_text('Notes')
    def tearDown(self):self.temp.cleanup()
    def write_report(self):(self.root/'ClearPage-3.0-build-report.json').write_text(json.dumps(self.report))
    def test_only_verified_production_artifacts_can_publish(self):
        self.assertEqual(publish_release.validate(self.root,'v3.0'),self.archive)
        for change in (dict(preview=True),dict(owner_test=True),dict(checks=[]),dict(sha256='wrong'),dict(version='4.0'),dict(desktop_check=None),dict(desktop_check=dict(renderer='mshtml',ui_ready=True,download_marks=True)),dict(desktop_check=dict(renderer='edgechromium',ui_ready=False,download_marks=True)),dict(desktop_check=dict(renderer='edgechromium',ui_ready=True,download_marks=False))):
            original=dict(self.report);self.report.update(change);self.write_report()
            with self.assertRaises(ValueError):publish_release.validate(self.root,'v3.0')
            self.report=original
        self.write_report();self.archive.write_bytes(b'tampered')
        with self.assertRaises(ValueError):publish_release.validate(self.root,'v3.0')
    def test_existing_and_older_releases_are_never_overwritten(self):
        for item in (dict(tag_name='v3.0',draft=True),dict(tag_name='v4.0',draft=False,prerelease=False)):
            with patch('scripts.publish_release.subprocess.check_output',return_value=json.dumps([item])),patch('scripts.publish_release.subprocess.run') as send:
                with self.assertRaises(ValueError):publish_release.publish(self.root,'v3.0','owner/repo')
                send.assert_not_called()
    def test_missing_or_changed_website_alias_cannot_publish(self):
        alias=self.root/'ClearPage-Windows-x64.zip'
        alias.write_bytes(b'unverified website package')
        with self.assertRaises(ValueError):publish_release.validate(self.root,'v3.0')
        alias.unlink()
        with self.assertRaises(ValueError):publish_release.validate(self.root,'v3.0')
    def test_missing_updater_rollback_check_blocks_publication(self):
        self.report['updater_check']['rollback']=False
        self.write_report()
        with self.assertRaises(ValueError):publish_release.validate(self.root,'v3.0')
    def test_assets_uploaded_before_draft_is_published(self):
        with patch('scripts.publish_release.subprocess.check_output',return_value='[]'),patch('scripts.publish_release.subprocess.run') as run:
            publish_release.publish(self.root,'v3.0','owner/repo')
            commands=[call.args[0] for call in run.call_args_list]
            self.assertEqual([cmd[2] for cmd in commands],['create','upload','edit'])
            self.assertIn('--draft',commands[0]);self.assertIn('--draft=false',commands[-1])
            self.assertNotIn('--clobber',commands[1])
            self.assertIn(str(self.root/'ClearPage-Windows-x64.zip'),commands[1])
    def test_missing_public_configuration_blocks_production_build(self):
        with patch.object(standard_release,'ROOT',self.root),patch.dict('os.environ',{'CLEARPAGE_PAYMENT_SERVICE_URL':'','CLEARPAGE_SIGNING_PUBLIC_KEY':''}):
            with self.assertRaises(ValueError):standard_release.public_config(False)
            self.assertEqual(standard_release.public_config(True)['payment_provider'],'razorpay')
    def test_unsafe_tags_are_rejected_before_any_network_action(self):
        for tag in ('../../bad','v3.0; echo bad','v3.0-beta','3.0'):
            with patch('scripts.publish_release.subprocess.check_output') as network:
                with self.assertRaises(ValueError):publish_release.publish(self.root,tag,'owner/repo')
                network.assert_not_called()
    def test_published_empty_stable_release_receives_verified_assets(self):
        item=dict(tag_name='v3.0',draft=False,prerelease=False,assets=[])
        with patch('scripts.publish_release.subprocess.check_output',return_value=json.dumps([item])),patch('scripts.publish_release.subprocess.run') as run:
            publish_release.publish(self.root,'v3.0','owner/repo',existing_release=True)
            commands=[call.args[0] for call in run.call_args_list]
            self.assertEqual([cmd[2] for cmd in commands],['upload'])
            self.assertNotIn('--clobber',commands[0])
    def test_main_publish_targets_exact_commit_and_skips_superseded_build(self):
        self.report['commit']='a'*40;self.write_report()
        with patch('scripts.publish_release.subprocess.check_output',side_effect=[json.dumps(dict(sha='a'*40)),'[]']),patch('scripts.publish_release.subprocess.run') as run:
            publish_release.publish(self.root,'v3.0','owner/repo',auto_main=True)
            self.assertIn('--target',run.call_args_list[0].args[0])
            self.assertIn('a'*40,run.call_args_list[0].args[0])
        with patch('scripts.publish_release.subprocess.check_output',return_value=json.dumps(dict(sha='b'*40))),patch('scripts.publish_release.subprocess.run') as run:
            publish_release.publish(self.root,'v3.0','owner/repo',auto_main=True)
            run.assert_not_called()
    def test_production_requires_https_and_preview_disables_payments(self):
        with patch.dict('os.environ',{'CLEARPAGE_BUILD_OWNER_TEST':'1'}):
            with self.assertRaises(ValueError):standard_release.run(preview=True)
        (self.root/'billing.json').write_text('{}')
        config=dict(payment_provider='razorpay',payment_service_url='http://127.0.0.1:5000')
        with patch.object(standard_release,'ROOT',self.root),patch.object(standard_release,'configuration',return_value=config),patch.object(standard_release,'payment_ready',return_value=True):
            with self.assertRaises(ValueError):standard_release.public_config(False)
            config['payment_service_url']='https://seller.example'
            self.assertEqual(standard_release.public_config(False),config)
            with self.assertRaises(ValueError):standard_release.public_config(True)
