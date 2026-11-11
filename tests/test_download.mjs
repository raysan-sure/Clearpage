import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const script = readFileSync(new URL('../website/download.js', import.meta.url), 'utf8');
async function clickDownload(response, hostname = 'clearpage.onrender.com') {
  let click;
  const status = { textContent: '' };
  const destinations = [];
  const dialog = { querySelector: () => status, showModal() { this.open = true; } };
  vm.runInNewContext(script, {
    document: {
      querySelectorAll: () => [{ addEventListener: (_, handler) => { click = handler; } }],
      createElement: () => dialog,
      body: { append() {} },
    },
    window: { location: { hostname, assign: url => destinations.push(url) } },
    AbortSignal,
    fetch: async () => { if (response instanceof Error) throw response; return response; },
  });
  await click({ preventDefault() {} });
  return { message: status.textContent, destinations };
}

test('no release stays on the website with an availability message', async () => {
  const result = await clickDownload({ status: 404 });
  assert.match(result.message, /first release has not been published/);
  assert.deepEqual(result.destinations, []);
});

test('only an uploaded stable Windows asset starts a download', async () => {
  const url = 'https://github.com/invicto-labs/Watermark-remover/releases/download/v2.2/ClearPage-Windows-x64.zip';
  const asset = { name: 'ClearPage-Windows-x64.zip', state: 'uploaded', size: 100, browser_download_url: url };
  const response = asset => ({ ok: true, json: async () => ({ draft: false, prerelease: false, assets: [asset] }) });
  assert.deepEqual((await clickDownload(response(asset))).destinations, [url]);
  for (const invalid of [{ ...asset, size: 0 }, { ...asset, state: 'new' }, { ...asset, browser_download_url: 'https://example.com/app.zip' }]) {
    assert.deepEqual((await clickDownload(response(invalid))).destinations, []);
  }
});

test('network errors stay on the website with a retry message', async () => {
  const result = await clickDownload(new Error('offline'));
  assert.match(result.message, /try again/);
  assert.deepEqual(result.destinations, []);
});
