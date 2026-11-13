"""Choose a newer stable build version for main; tagged releases keep their version."""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clearpage.updates import parse_version
from scripts.release import version
from scripts.set_version import set_version


def next_version(source, latest):
    if latest is None or parse_version(source) > parse_version(latest):
        return source
    major, minor, patch = parse_version(latest)
    return f'{major}.{minor}.{patch+1}'


def main():
    ref = os.environ.get('GITHUB_REF', '')
    if ref.startswith('refs/tags/'):
        tag = ref.removeprefix('refs/tags/')
        if not tag.startswith('v'):
            raise ValueError('Windows releases require a numeric v-prefixed tag.')
        parse_version(tag[1:])
        set_version(tag[1:])
    value = version()
    if os.environ.get('GITHUB_REF') == 'refs/heads/main' and os.environ.get('GITHUB_EVENT_NAME') == 'push':
        repo = os.environ['GITHUB_REPOSITORY']
        req = urllib.request.Request(f'https://api.github.com/repos/{repo}/releases/latest',
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ClearPage-CI',
                     'Authorization': 'Bearer '+os.environ['GH_TOKEN']})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                latest = json.load(response)['tag_name']
            if not isinstance(latest, str) or not latest.startswith('v'):
                raise ValueError('Latest stable release must use a numeric version tag.')
            latest = latest[1:]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            latest = None
        value = next_version(value, latest)
        set_version(value)
    tag = 'v'+value
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write(f'tag={tag}\n')
    print('Prepared Windows build:', tag)


if __name__ == '__main__':
    main()
