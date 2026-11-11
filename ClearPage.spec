# Build from the repository root in a clean Windows virtual environment.
from pathlib import Path
import os
import hashlib
from PyInstaller.utils.hooks import collect_all, collect_delvewheel_libs_directory

root = Path(SPECPATH)
# Opt-in commercial builds include public payment instructions and a public key only.
from licensing import configuration, payment_ready
owner_test = os.environ.get('CLEARPAGE_BUILD_OWNER_TEST') == '1'
billing_path = Path(os.environ['CLEARPAGE_BUILD_OWNER_TEST_CONFIG']) if owner_test else root / 'billing.json'
preview = os.environ.get('CLEARPAGE_BUILD_PREVIEW') == '1'
standard = os.environ.get('CLEARPAGE_BUILD_STANDARD') == '1'
if owner_test:
    from urllib.parse import urlsplit
    owner_config = configuration(billing_path)
    if not standard or preview or owner_config.get('payment_provider')!='razorpay' or owner_config.get('payment_service_url')!='http://127.0.0.1:5056':
        raise ValueError('Private owner test builds require the exact local test service and cannot be release/preview builds.')
    os.environ['CLEARPAGE_PAYMENT_LOCAL_TEST'] = '1'
if billing_path.exists() and not payment_ready(configuration(billing_path)) and not preview:
    raise ValueError('Complete billing.json before distributing a paid build. See BILLING.md.')
datas = [(str(root/'assets/repeated-copy.png'), 'assets'), (str(root/'static'), 'static'), (str(root/'assets/clearpage.ico'), 'assets'), (str(root/'assets/huntington.npy'), 'assets'),
         (str(root/'assets/nassau-border.npy'), 'assets'),
         (str(root/'assets/preble-unofficial.npz'), 'assets'),
         (str(root/'assets/cumberland-red.npz'), 'assets'),
         (str(root/'assets/screened-unofficial.npz'), 'assets'),
         (str(root/'assets/kane-outline.npz'), 'assets'),
         (str(root/'assets/licensed-diagonal.npz'), 'assets'),
         (str(root/'assets/hocking-outline.png'), 'assets'),
         (str(root/'assets/hocking-layouts.json'), 'assets'),
         (str(root/'assets/search-package-dot-probes.npz'), 'assets'),
         (str(root/'assets/tammany-unofficial-probes.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-1994.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-1019.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-1032.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-2019.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-2024.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-2029.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-2536.npz'), 'assets'),
         (str(root/'assets/unofficial-copy-2538.npz'), 'assets'),
         (str(root/'assets/ocr-word.ps1'), 'assets')]
if not standard:
    datas += [(str(root/'models/big-lama.pt'), 'models'),
              (str(root/'models/PP-OCRv6_rec_small.onnx'), 'models')]
binaries, hiddenimports = [], []
if not billing_path.exists():
    raise ValueError('Customer builds require billing.json. A missing config must never create a free customer build.')
policy_dir = root / 'build' / ('owner-test-policy' if owner_test else 'customer-policy')
policy_dir.mkdir(parents=True, exist_ok=True)
policy_hook = policy_dir / 'enforce_paid.py'
policy = 'import licensing\nlicensing.FROZEN_CONFIG_HASH = ' + repr(hashlib.sha256(billing_path.read_bytes()).hexdigest()) + '\n'
if owner_test:
    from scripts.owner_test_build import profile_policy
    policy += profile_policy(os.environ['CLEARPAGE_BUILD_OWNER_TEST_PROFILE'])
policy_hook.write_text(policy, encoding='utf-8')
from scripts.windows_branding import version_resource
version_path = policy_dir/'version-info.txt'
version_resource(version_path, owner_test)
if billing_path.exists():
    datas.append((str(billing_path), '.'))
for package in ('pypdfium2', 'pypdfium2_raw', 'pikepdf'):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += imports
datas, binaries = collect_delvewheel_libs_directory('pikepdf', datas=datas, binaries=binaries)

a = Analysis([str(root/'app.py')], pathex=[str(root)], binaries=binaries,
             runtime_hooks=[str(policy_hook)],
             datas=datas, hiddenimports=hiddenimports,
             excludes=['matplotlib', 'pandas', 'scipy', 'IPython', 'notebook', 'pytest',
                       'payment_service', 'scripts.license_admin'] + (['torch', 'onnxruntime'] if standard else []),
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ClearPage',
          console=False, strip=False, upx=False, icon=str(root/'assets/clearpage.ico'), version=str(version_path))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ClearPage')
# The CLR reads the host configuration beside the executable, outside _internal.
import shutil
shutil.copy2(root/'assets/ClearPage.exe.config', Path(coll.name)/'ClearPage.exe.config')
