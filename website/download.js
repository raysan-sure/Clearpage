// Check publication before leaving the website for a GitHub download.
const releaseApi = 'https://api.github.com/repos/raysan-sure/Clearpage/releases/latest';
const previewTag = 'v2.3-preview.1';
const previewApi = `https://api.github.com/repos/raysan-sure/Clearpage/releases/tags/${previewTag}`;
const downloadPrefix = 'https://github.com/raysan-sure/Clearpage/releases/download/';
const links = document.querySelectorAll('a[href$="/ClearPage-Windows-x64.zip"]');
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
      const options = () => ({
        headers: { Accept: 'application/vnd.github+json' },
        signal: AbortSignal.timeout(10000),
        credentials: 'omit',
      });
      let response = await fetch(releaseApi, options());
      const preview = response.status === 404;
      if (preview) response = await fetch(previewApi, options());
      if (response.status === 404) {
        notice.querySelector('[role="status"]').textContent = 'The Windows download is not available yet. The first release has not been published. Please check back later.';
        return;
      }
      if (!response.ok) throw new Error('Release check failed');
      const release = await response.json();
      const allowedRelease = release.draft === false && (preview
        ? release.prerelease === true && release.tag_name === previewTag
        : release.prerelease === false);
      const asset = allowedRelease && Array.isArray(release.assets)
        ? release.assets.find(item => item.name === 'ClearPage-Windows-x64.zip'
          && item.state === 'uploaded' && item.size > 0
          && typeof item.browser_download_url === 'string'
          && item.browser_download_url.startsWith(downloadPrefix)
          && (!preview || item.browser_download_url === `${downloadPrefix}${previewTag}/ClearPage-Windows-x64.zip`))
        : null;
      if (!asset) {
        notice.querySelector('[role="status"]').textContent = 'The latest release does not have a Windows download ready yet. Please check back later.';
        return;
      }
      notice.querySelector('[role="status"]').textContent = preview
        ? 'Your Windows preview download is starting. Extract the ZIP, then open ClearPage.exe. PDF processing remains locked until payment hosting is configured.'
        : 'Your Windows download is starting. Extract the ZIP, then open ClearPage.exe.';
      window.location.assign(asset.browser_download_url);
    } catch {
      notice.querySelector('[role="status"]').textContent = 'We could not check the Windows download. Please check your connection and try again in a moment.';
    } finally {
      checking = false;
    }
  });
}
