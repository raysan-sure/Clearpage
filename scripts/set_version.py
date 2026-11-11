"""Prepare a version bump locally; does not commit, tag, push or publish."""
import argparse
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def set_version(value):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',value):
        raise ValueError('Use a stable numeric version such as 2.3.0.')
    path=ROOT/'clearpage/app_version.py'
    text,count=re.subn(r"^VERSION = '[^']+'$",f"VERSION = '{value}'",path.read_text(),flags=re.M)
    if count != 1:raise ValueError('Expected exactly one VERSION assignment.')
    readme=ROOT/'README.md'
    lines=readme.read_text(encoding='utf-8').splitlines(keepends=True)
    if not lines or not lines[0].startswith('# ClearPage '):raise ValueError('Unexpected README heading.')
    lines[0]=f'# ClearPage {value}\n'
    path.write_text(text,encoding='utf-8');readme.write_text(''.join(lines),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('version')
    set_version(parser.parse_args().version)
