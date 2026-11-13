"""Dedicated Windows window around the local PDF service; no remote Python bridge."""
import sys
from clearpage.paths import RESOURCE_ROOT
from pathlib import Path
from clearpage.app_version import VERSION


def open_window(url):
    if sys.platform != 'win32':
        raise RuntimeError('The ClearPage desktop edition requires Windows.')
    import webview
    set_app_identity()
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
    window = webview.create_window(f'ClearPage {VERSION}', url, width=1280, height=850,
                          min_size=(860, 620), background_color='#faf8f4',
                          text_select=True, js_api=None)
    renderer_state = {'supported': False}
    def require_supported_renderer(renderer):
        renderer_state['supported'] = renderer == 'edgechromium'
        return renderer_state['supported']
    # pywebview can otherwise fall back to MSHTML even when Edge was requested.
    # This synchronous event cancels window creation before that fallback appears.
    window.events.initialized += require_supported_renderer
    if '--update-health-file' in sys.argv:
        import time
        from clearpage.auto_update import healthy_ui
        def update_loaded():
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                if window.evaluate_js("Boolean(document.querySelector('#root button'))"):
                    healthy_ui()
                    return
                time.sleep(.1)
        window.events.loaded += update_loaded
    if '--desktop-smoke-info' in sys.argv:
        import json
        import time
        info = Path(sys.argv[sys.argv.index('--desktop-smoke-info')+1])
        def verify_loaded_page():
            try:
                deadline = time.monotonic()+15
                while time.monotonic() < deadline:
                    ready = window.evaluate_js("Boolean(document.querySelector('#root button'))")
                    if ready:
                        info.write_text(json.dumps(dict(renderer=webview.renderer,
                            title=window.evaluate_js('document.title'), ui_ready=True)), encoding='utf-8')
                        break
                    time.sleep(.1)
            finally:
                window.destroy()
        window.events.loaded += verify_loaded_page
    webview.start(gui='edgechromium', debug=False, private_mode=True,
                  icon=str(RESOURCE_ROOT/'assets/clearpage.ico'))
    if not renderer_state['supported']:
        raise RuntimeError('Microsoft Edge WebView2 Runtime is required.')


def close_window():
    import webview
    if not webview.windows:
        raise RuntimeError('The desktop window is not ready to restart.')
    webview.windows[0].destroy()


def set_app_identity():
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('RaysanSure.ClearPage')


def write_startup_log():
    import os
    import traceback
    path = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'ClearPage'/'startup-error.log'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding='utf-8')
        return path
    except OSError:
        return None


def show_startup_error(log_path=None):
    message = ('ClearPage could not open its desktop window.\n\n'
               'Extract the complete ZIP, keeping _internal beside ClearPage.exe. '
               'Keep ClearPage.exe.config beside it too. '
               'Windows 10/11 x64 and Microsoft Edge WebView2 Runtime are required.\n\n'
               'WebView2: https://developer.microsoft.com/microsoft-edge/webview2/\n'
               'If the problem continues, contact sales@zynape.com.')
    if log_path:
        message += f'\n\nStartup details: {log_path}'
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'ClearPage — unable to start', 0x10)
    else:
        print(message, file=sys.stderr)
