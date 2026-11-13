"""Build and verify the current Standard edition; public config only, no payment secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clearpage.billing.licensing import configuration, payment_ready
from scripts.release import collect_licenses, version, digest


def public_config(preview=False):
    if not (ROOT/'billing.json').exists():
        value = dict(payment_provider='razorpay', payment_service_url=os.environ.get('CLEARPAGE_PAYMENT_SERVICE_URL',''),
                     public_key=os.environ.get('CLEARPAGE_SIGNING_PUBLIC_KEY',''),
                     seller_name=os.environ.get('CLEARPAGE_SELLER_NAME','Raysan sure'),
                     support_email=os.environ.get('CLEARPAGE_SUPPORT_EMAIL','sales@zynape.com'))
        (ROOT/'billing.json').write_text(json.dumps(value, indent=2), encoding='utf-8')
    config = configuration(ROOT/'billing.json')
    if not preview and urlsplit(config.get('payment_service_url','')).scheme != 'https':
        raise ValueError('Production packages require HTTPS, even when localhost test mode is enabled.')
    if preview and payment_ready(config):
        raise ValueError('Preview builds require disabled payment settings. Use a separate review checkout rather than distributing active payment configuration.')
    if config.get('payment_provider') != 'razorpay' or (not preview and not payment_ready(config)):
        raise ValueError('Production releases require complete public Razorpay billing settings. No merchant secrets belong in CI.')
    return config


def run(preview=False, dist_dir=None, work_dir=None, assets_dir=None):
    if os.environ.get('CLEARPAGE_BUILD_OWNER_TEST') == '1':
        raise ValueError('Private owner test builds cannot use the customer release pipeline.')
    value = version()
    public_config(preview)
    if sys.platform != 'win32' or sys.version_info[:2] != (3,12):
        raise RuntimeError('Build on Windows with Python 3.12.')
    dist_dir = Path(dist_dir or ROOT/'dist').resolve()
    work_dir = Path(work_dir or ROOT/'build/standard').resolve()
    if (dist_dir/'ClearPage').exists():
        raise ValueError('Choose a fresh output directory; existing packages are never replaced by this script.')
    env = {**os.environ, 'CLEARPAGE_BUILD_STANDARD':'1', 'CLEARPAGE_BUILD_PREVIEW':'1' if preview else '0'}
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(dist_dir),
                    '--workpath',str(work_dir), 'ClearPage.spec'],cwd=ROOT,env=env,check=True)
    bundle = dist_dir/'ClearPage'
    collect_licenses(bundle, standard=True)
    (bundle/'START-HERE.txt').write_text(
        f'ClearPage {value} Standard — Windows x64\n\nExtract the complete ZIP and open ClearPage.exe. Keep _internal and ClearPage.exe.config beside it.\n'
        'Internet is required for online credits and document authorization.\n'
        'ClearPage opens in its own desktop window. Windows 10/11 x64 and Microsoft Edge WebView2 Runtime are required.\n'
        'If WebView2 is missing, install it from https://developer.microsoft.com/microsoft-edge/webview2/ then reopen ClearPage.\n'
        'Updates download and verify automatically. ClearPage saves open work and restarts after a minute without interaction. Keep working to postpone. Your existing profile is preserved.\n'
        'Optional LaMa/ONNX models are not included. This executable is unsigned. Review cleanup output carefully.\n'
        + ('Preview: payments are not enabled until the seller finishes configuration.\n' if preview else ''), encoding='utf-8')
    assets = Path(assets_dir or ROOT/'release-assets').resolve()
    from clearpage.update_install import ENTRIES
    (bundle/'package-manifest.json').write_text(json.dumps(dict(format=1, edition='Standard', version=value,
        entries=[name for name in ENTRIES if name == 'package-manifest.json' or (bundle/name).exists()])), encoding='utf-8')
    assets.mkdir(exist_ok=False)
    archive = assets/f'ClearPage-{value}-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for file in sorted(bundle.rglob('*')):
            if file.is_file(): output.write(file,Path('ClearPage')/file.relative_to(bundle))
    extracted = work_dir/'extracted'
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None: raise ValueError('ZIP integrity failure.')
        if any(n.endswith(('.pem','.sqlite3','settings.json')) or '/payment_service/' in n for n in source.namelist()):
            raise ValueError('Private seller file found in customer package.')
        source.extractall(extracted)
    from scripts.standard_smoke import check_package, check_desktop
    checks = check_package(extracted/'ClearPage', value)
    desktop_check = check_desktop(extracted/'ClearPage')
    from scripts.update_smoke import check_updater
    updater_check = check_updater(archive)
    report = dict(version=value, edition='Standard', preview=preview,
                  package=archive.name, sha256=digest(archive), checks=checks, desktop_check=desktop_check, updater_check=updater_check,
                  commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  publisher_signature='unsigned', limitation='Simulated payment/source regressions do not establish live Razorpay activation.')
    report['owner_test'] = False
    (assets/f'ClearPage-{value}-build-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    alias = assets/'ClearPage-Windows-x64.zip'
    shutil.copy2(archive, alias)
    (assets/f'ClearPage-{value}-SHA256.txt').write_text(
        f'{digest(archive)}  {archive.name}\n{digest(alias)}  {alias.name}\n',encoding='ascii')
    (assets/'release-notes.md').write_text(f'ClearPage {value} Standard for Windows x64.\n\n'
        'Download the complete ZIP and extract it into a new folder after closing the old version. '
        'Keep your existing ClearPage profile to preserve online account access. '
        'Future stable updates download and verify automatically; open work is saved and restored across an idle restart, with rollback if the new desktop fails. '
        'Opens in a dedicated Windows window using Microsoft Edge WebView2 Runtime. '
        'Optional neural models are not included. The executable is unsigned. '
        'See the attached build report and checksum.\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--dist-dir')
    parser.add_argument('--work-dir')
    parser.add_argument('--assets-dir')
    args=parser.parse_args()
    run(args.preview,args.dist_dir,args.work_dir,args.assets_dir)
