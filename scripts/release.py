"""Repeatable Windows build, with verification of the ZIP employees receive."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
import ast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def version():
    tree = ast.parse((ROOT/'clearpage/app_version.py').read_text(encoding='utf-8'))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)]
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', values[0]):
        raise ValueError('Set a stable numeric VERSION in app_version.py.')
    value = values[0]
    source = (ROOT/'app.py').read_text(encoding='utf-8')
    if f"_ClearPage-{value}.pdf" not in source and '_ClearPage-{VERSION}.pdf' not in source:
        raise ValueError('The exported PDF version in app.py must match the interface.')
    if not (ROOT/'README.md').read_text(encoding='utf-8').startswith(f'# ClearPage {value}\n'):
        raise ValueError('Update the README heading to match the application version.')
    tag = os.environ.get('RELEASE_TAG', '')
    if tag and tag != 'v'+value:
        raise ValueError(f'Release tag must be v{value}, matching the checked-out application.')
    return value


def command(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect_licenses(bundle, standard=False):
    """Collect original notices, including those nested inside wheel packages."""
    target = bundle/'licenses'
    target.mkdir()
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file():
        raise FileNotFoundError('Python LICENSE.txt is required for distribution.')
    shutil.copy2(python_license, target/'Python-LICENSE.txt')
    for source in (ROOT/'vendor').iterdir():
        if source.suffix.lower() in ('.txt', '.md'):
            shutil.copy2(source, target/source.name)
    installed = []
    required = {'pypdfium2', 'pikepdf', 'lxml', 'opencv-python-headless', 'numpy', 'pillow', 'cryptography', 'flask'}
    if not standard:
        required.update(('torch', 'onnxruntime'))
    if sys.platform == 'win32':
        required.update(('pywebview', 'pythonnet', 'clr-loader'))
    covered = set()
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', name)
        destination = target/f'{safe_name}-{dist.version}'
        count = 0
        for relative in dist.files or []:
            path = Path(str(relative))
            # RECORD paths may start ../../; never carry those out of the bundle.
            if '..' in path.parts or path.is_absolute():
                continue
            # Installed bytecode can retain the builder's absolute home directory.
            # It is executable cache, not a third-party license notice.
            if '__pycache__' in path.parts or path.suffix.lower() in ('.pyc', '.pyo'):
                continue
            if not any(re.search(r'license|licence|notice|copying|copyright', p, re.I) for p in path.parts):
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file():
                continue
            dest = destination/path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            count += 1
        destination.mkdir(parents=True, exist_ok=True)
        (destination/'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
        installed.append(f'{name}=={dist.version}')
        if count:
            covered.add(name.lower().replace('_', '-'))
    if missing := required-covered:
        raise ValueError(f'Missing bundled dependency licenses: {sorted(missing)}')
    (bundle/'DEPENDENCIES.txt').write_text('\n'.join(sorted(installed))+'\n', encoding='utf-8')
    (bundle/'THIRD-PARTY-NOTICES.txt').write_text(
        ('ClearPage Standard bundles Python and open-source libraries; optional neural models are omitted.\n' if standard else 'ClearPage bundles Python, open-source libraries, LaMa and PP-OCR recognition weights.\n') +
        'Original license texts, model provenance and modification notices are in licenses/.\n'
        'See DEPENDENCIES.txt for the installed build environment versions.\n'
        'PDFium notices also remain inside _internal. Windows fonts are not redistributed.\n',
        encoding='utf-8')


def build(value):
    if sys.platform != 'win32' or sys.maxsize <= 2**32:
        raise RuntimeError('Build on Windows x64 with Python 3.12.')
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('Python 3.12 is required.')
    from clearpage.restore.neural_restore import verify_model
    from clearpage.restore.text_restore import MODEL_NAME, MODEL_SHA256
    verify_model(ROOT/'models/big-lama.pt')
    if digest(ROOT/'models'/MODEL_NAME) != MODEL_SHA256:
        raise ValueError('Text model hash mismatch.')
    # Refuse stale outputs instead of accidentally shipping files from an old build.
    if (ROOT/'dist/ClearPage').exists():
        raise FileExistsError('dist/ClearPage already exists. Use a fresh checkout or build environment.')
    command(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', 'ClearPage.spec')
    bundle = ROOT/'dist/ClearPage'
    collect_licenses(bundle)
    shutil.copy2(ROOT/'README.md', bundle/'README.md')
    shutil.copytree(ROOT/'docs', bundle/'docs')
    (bundle/'START-HERE.txt').write_text(
        f'ClearPage {value} - Windows x64\n\n'
        'Extract the complete ZIP, then open ClearPage/ClearPage.exe.\n'
        'Keep the _internal folder beside the executable. No Python installation is needed.\n'
        'Open a PDF, JPG, PNG or TIFF. Clean, compare Before/After, accept the pages\n'
        'you want to use, then Create PDF and Download PDF. Your input is not overwritten.\n'
        'Read README.md for cleanup options and limits. Review important text before use.\n',
        encoding='utf-8')


def verify(value):
    from smoke_package import check_package
    bundle = ROOT/'dist/ClearPage'
    staging = ROOT/'build/release-staging'
    staging.mkdir(parents=True, exist_ok=False)
    archive = staging/f'ClearPage-{value}-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for file in sorted(bundle.rglob('*')):
            if file.is_file():
                output.write(file, Path('ClearPage')/file.relative_to(bundle))
    extracted = staging/'extracted'
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise ValueError('ZIP integrity check failed.')
        source.extractall(extracted)
    result = check_package(extracted/'ClearPage', value)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    report = dict(version=value, commit=commit, python=sys.version,
                  run_url=(f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/'
                           f'{os.environ["GITHUB_RUN_ID"]}' if os.environ.get('GITHUB_RUN_ID') else None),
                  package=archive.name, sha256=digest(archive), checks=result,
                  limitation='Synthetic build checks do not measure accuracy on company documents.')
    benchmark_path=ROOT/'build/benchmark/benchmark-report.json'
    if not benchmark_path.is_file():
        raise ValueError('Run scripts/benchmark_suite.py --output-dir build/benchmark before verification.')
    benchmark_report=json.loads(benchmark_path.read_text(encoding='utf-8'))
    if benchmark_report.get('gate_failures') != []:
        raise ValueError('Benchmark gates did not pass.')
    report['benchmark']={'pages':len(benchmark_report['pages']), 'categories':len(benchmark_report['categories']), 'passed':True}
    report['publisher_signature']='unsigned - signing deferred'
    # Nothing becomes publishable until the extracted executable passes every check.
    assets = ROOT/'release-assets'
    assets.mkdir(exist_ok=False)
    shutil.copy2(archive, assets/archive.name)
    report_path = assets/f'ClearPage-{value}-build-report.json'
    report_path.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    benchmark_target=assets/f'ClearPage-{value}-benchmark-report.json'
    shutil.copy2(benchmark_path,benchmark_target)
    (assets/f'ClearPage-{value}-SHA256.txt').write_text(
        f'{report["sha256"]}  {archive.name}\n{digest(report_path)}  {report_path.name}\n{digest(benchmark_target)}  {benchmark_target.name}\n', encoding='utf-8')
    print('Verified Windows ZIP, build report and checksums:', assets)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'build', 'verify'])
    action = parser.parse_args().action
    value = version()
    print('ClearPage version:', value, flush=True)
    if action == 'build':
        build(value)
    elif action == 'verify':
        verify(value)
