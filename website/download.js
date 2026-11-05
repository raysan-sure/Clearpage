// Check publication before leaving the website for a GitHub download.
const releaseApi = 'https://api.github.com/repos/raysan-sure/Clearpage/releases/latest';
const downloadPrefix = 'https://github.com/raysan-sure/Clearpage/releases/download/';
const links = document.querySelectorAll('a[href$="/releases/latest/download/ClearPage-Windows-x64.zip"]');
const notice = document.createElement('dialog');
notice.className = 'download-notice';
notice.innerHTML = '<h2>ClearPage download</h2><p role="status"></p><p>For help, email <a href="mailto:sales@zynape.com">sales@zynape.com</a>.</p><form method="dialog"><button class="button">Close</button></form>';
document.body.append(notice);

let checking = false;
for (const link of links) {
  link.addEventListener('click', async event => {
    event.preventDefault();
    if (checking) return;
    checking = true;
    notice.querySelector('[role="status"]').textContent = 'Checking the latest Windows download…';
    if (!notice.open) notice.showModal();
    try {
      const response = await fetch(releaseApi, {
        headers: { Accept: 'application/vnd.github+json' },
        signal: AbortSignal.timeout(10000),
        credentials: 'omit',
      });
      if (response.status === 404) {
        notice.querySelector('[role="status"]').textContent = 'The Windows download is not available yet. The first release has not been published. Please check back later.';
        return;
      }
      if (!response.ok) throw new Error('Release check failed');
      const release = await response.json();
      const asset = !release.draft && !release.prerelease && Array.isArray(release.assets)
        ? release.assets.find(item => item.name === 'ClearPage-Windows-x64.zip'
          && item.state === 'uploaded' && item.size > 0
          && typeof item.browser_download_url === 'string'
          && item.browser_download_url.startsWith(downloadPrefix))
        : null;
      if (!asset) {
        notice.querySelector('[role="status"]').textContent = 'The latest release does not have a Windows download ready yet. Please check back later.';
        return;
      }
      notice.querySelector('[role="status"]').textContent = 'Your Windows download is starting. Extract the ZIP, then open ClearPage.exe.';
      window.location.assign(asset.browser_download_url);
    } catch {
      notice.querySelector('[role="status"]').textContent = 'We could not check the Windows download. Please check your connection and try again in a moment.';
    } finally {
      checking = false;
    }
  });
}
