"""Bounded, cached GitHub release checks; never download or execute application code."""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from app_version import VERSION

REPOSITORY = 'raysan-sure/Clearpage'
RELEASES = f'https://github.com/{REPOSITORY}/releases'
_cache = {}
_lock = threading.Lock()


def parse_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
        raise ValueError('Invalid stable version.')
    parts = tuple(int(part) for part in value.split('.'))
    return (parts + (0, 0, 0))[:3]


def describe(release):
    result = dict(available=False, current_version=VERSION, url=RELEASES,
                  message='You have the latest available stable version.')
    tag = release.get('tag_name')
    if (not isinstance(tag, str) or not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?', tag)
            or release.get('draft') is not False or release.get('prerelease') is not False):
        return result
    value = tag[1:]
    if parse_version(value) <= parse_version(VERSION):
        return result
    package = f'ClearPage-{value}-Windows-x64.zip'
    download = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{package}'
    assets = release.get('assets')
    if not isinstance(assets, list):
        return result
    ready = any(isinstance(asset, dict) and asset.get('name') == package
                and asset.get('state') == 'uploaded' and type(asset.get('size')) is int
                and asset['size'] > 0 and asset.get('browser_download_url') == download
                for asset in assets)
    if not ready:
        result['message'] = 'A newer release exists, but its Windows download is not ready.'
        return result
    result.update(available=True, version=value, package=package, download_url=download,
                  url=f'{RELEASES}/tag/{tag}', notes=str(release.get('body') or '')[:2000],
                  message=f'ClearPage {value} is available.')
    return result


def latest_release():
    # One request per running app per six hours; failures are cached for one minute.
    with _lock:
        if _cache and time.monotonic() < _cache['until']:
            return dict(_cache['result'])
        req = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/releases/latest',
            headers={'Accept':'application/vnd.github+json', 'User-Agent':'ClearPage/'+VERSION})
        ttl = 21600
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('Response too large.')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Unexpected release response.')
            result = describe(data)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                result = dict(available=False, current_version=VERSION, url=RELEASES,
                              message='No published Windows releases yet.')
            else:
                ttl = 60
                result = dict(available=False, current_version=VERSION, error=True,
                              message='Could not check for updates. Try again shortly.')
        except (OSError, ValueError, TypeError):
            ttl = 60
            result = dict(available=False, current_version=VERSION, error=True,
                          message='Could not check for updates. Try again when connected.')
        _cache.update(result=result, until=time.monotonic()+ttl)
        return dict(result)
