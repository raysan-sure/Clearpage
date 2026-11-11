"""Desktop lifetime, renderer and local-only startup contracts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import app
from clearpage import desktop_shell


class DesktopTests(unittest.TestCase):
    def view(self, renderer):
        class Event:
            def __iadd__(self, callback):
                self.callback=callback
                return self
        event=Event()
        view=MagicMock();view.settings={}
        view.create_window.return_value=SimpleNamespace(events=SimpleNamespace(initialized=event))
        view.start.side_effect=lambda **kwargs: event.callback(renderer)
        return view

    def test_window_enables_exports_without_exposing_a_python_api(self):
        view=self.view('edgechromium')
        with patch.dict(sys.modules,webview=view),patch.object(sys,'platform','win32'),patch.object(desktop_shell,'set_app_identity'):
            desktop_shell.open_window('http://127.0.0.1:12345/')
        self.assertTrue(view.settings['ALLOW_DOWNLOADS'])
        self.assertFalse(view.settings['ALLOW_FILE_URLS'])
        self.assertTrue(view.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'])
        self.assertIsNone(view.create_window.call_args.kwargs['js_api'])
        view.start.assert_called_once_with(gui='edgechromium',debug=False,private_mode=True,icon=str(app.BASE/'assets/clearpage.ico'))

    def test_legacy_renderer_is_cancelled_instead_of_opening(self):
        view=self.view('mshtml')
        with patch.dict(sys.modules,webview=view),patch.object(sys,'platform','win32'),patch.object(desktop_shell,'set_app_identity'):
            with self.assertRaises(RuntimeError):desktop_shell.open_window('http://127.0.0.1:12345/')
        self.assertFalse(view.create_window.return_value.events.initialized.callback('mshtml'))

    def launch(self, fail=False, test_info=None):
        server=MagicMock();server.server_port=12345
        previous=(app.SERVER,app.HOST,app.WORK['stop'])
        argv=['ClearPage.exe']+(['--test-info',str(test_info)] if test_info else [])
        try:
            with patch.object(sys,'argv',argv),patch.object(app,'make_server',return_value=server) as make,patch.object(app.threading,'Thread') as thread,patch.object(app,'cleanup'),patch('webbrowser.open') as browser,patch.object(desktop_shell,'write_startup_log',return_value=Path('startup-error.log')),patch.object(desktop_shell,'show_startup_error') as error,patch.object(desktop_shell,'open_window',side_effect=RuntimeError('missing runtime') if fail else None) as window:
                if fail:
                    with self.assertRaises(SystemExit):app.main()
                    error.assert_called_once_with(Path('startup-error.log'))
                else:app.main()
                browser.assert_not_called()
                make.assert_called_once_with('127.0.0.1',0,app.app,threaded=True,request_handler=app.QuietHandler)
                if test_info:
                    window.assert_not_called();thread.assert_not_called()
                else:
                    window.assert_called_once_with('http://127.0.0.1:12345/')
                    thread.return_value.start.assert_called_once()
                    server.shutdown.assert_called_once()
                server.server_close.assert_called_once()
        finally:
            app.SERVER,app.HOST,app.WORK['stop']=previous

    def test_close_stops_local_service_without_launching_browser(self):self.launch()
    def test_renderer_failure_closes_service_without_browser_fallback(self):self.launch(fail=True)
    def test_headless_build_check_remains_available(self):
        with tempfile.TemporaryDirectory() as folder:
            info=Path(folder)/'info.json'
            self.launch(test_info=info)
            self.assertEqual(json.loads(info.read_text())['url'],'http://127.0.0.1:12345/')
