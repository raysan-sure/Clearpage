# Maintaining ClearPage releases

Keep application source in Git. Put employee ZIP packages in GitHub Releases;
the Windows package and model weights are too large for ordinary Git commits.
Customer PDFs, repaired pages, local session details, and downloaded model
weights must remain outside the repository.

## Verify a change

Use Windows x64 and Python 3.12. From the application source directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
.\.venv\Scripts\python.exe download_model.py
.\.venv\Scripts\python.exe download_text_model.py
.\.venv\Scripts\python.exe -m unittest tests.test_app
.\.venv\Scripts\python.exe -m unittest tests.test_overlays
.\.venv\Scripts\python.exe -m unittest tests.test_quality
.\.venv\Scripts\python.exe -m unittest tests.test_scans
.\.venv\Scripts\python.exe app.py
```

The download scripts verify the model hashes before installing them. These
downloads require internet access; normal document processing is local.
The tests create synthetic documents and do not require customer files.
Test the browser workflow, page selection, word repair, and exported PDF before
distributing a changed build. Test results are not a universal accuracy claim.

## Build for employees

Use a fresh checkout and virtual environment with the pinned dependencies above.
Do not mix dependencies from older build folders. The same build scripts used
by GitHub Actions can run locally:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -v -p 'test_*.py'
.\.venv\Scripts\python.exe scripts/release.py check
.\.venv\Scripts\python.exe scripts/release.py build
.\.venv\Scripts\python.exe scripts/release.py verify
```

The verified Windows ZIP, SHA-256 file and build report appear in `release-assets/`.
The scripts refuse stale output folders; use a fresh checkout for each build.
Keep the entire generated `dist/ClearPage` folder, including `_internal`.

Before distribution, include README.md, the vendor attribution/license files,
and the licenses/notices for Python and all bundled dependencies. Keep PDFium,
PyTorch, ONNX Runtime, and model notices with the package. Follow the complete
license terms in those files. Windows fonts are read from the employee's
computer and must not be copied into the package.

Test the packaged ClearPage.exe on Windows as well as running the source tests.
Open a PDF, preview, process, exclude a page, export, reopen the result, and close
the app. Extract and test the final ZIP so missing files are caught. Record the
ZIP's SHA-256 with `Get-FileHash -Algorithm SHA256 <zip-path>`.

## Automatic GitHub releases

The **Windows release** workflow in `.github/workflows/windows-release.yml`
runs when a GitHub release is published, including a prerelease. It checks out
that release's exact tag, uses Windows Server 2022 x64 and Python 3.12.10 (the
last Python 3.12 Windows installer offered by setup-python), and:

1. Checks the tag against the app version, runs every `test_*.py` regression test.
2. Downloads both offline models and verifies their pinned hashes.
3. Builds the Windows app with PyInstaller and collects dependency/model notices.
4. Creates the employee ZIP, extracts it, runs LaMa and ONNX inference inside
   the packaged executable, and exercises PDF, JPG, PNG and multipage TIFF
   import, correction, selective export, unchanged pages and clean shutdown.
5. Saves the ZIP, checksums and build report as a 14-day Actions artifact.
6. Attaches those verified files to the release. A failed build uploads no
   release assets. It does not delete the release or overwrite existing assets.

Only the final attachment job receives `contents: write`. Build steps use
read-only repository access. No personal access token or customer files are
needed. Dependencies and models need internet during building; the app stays
local during normal use. The package is unsigned; code signing and automatic
employee updates are separate features.

### Publish a version

1. Commit and push the reviewed source changes. Update both version strings
   in `static/index.html`, the download name in `app.py`, the README heading,
   and release notes together. Run `python scripts/release.py check`.
2. In the repository's **Releases**, choose **Draft a new release**. Select the
   exact tested commit and a new version tag matching the code, currently `v2.2`.
   Do not move an existing release tag.
3. Describe the changes and publish. Under **Actions → Windows release**, watch
   the build. The Windows ZIP, SHA-256 and build report are attached only after
   it succeeds; publishing the release itself does not mean the binary is ready.
4. GitHub supplies source archives for the tag. The employee ZIP includes the
   offline model weights; source users obtain them through the download scripts.

### Test the automation without publishing

Open **Actions → Windows release → Run workflow**, select the branch to test,
and run it. Download `ClearPage-Windows-verified` under the completed run's
**Artifacts**. A manual run builds and tests but does not create a tag, publish
a release or attach files. Test the browser experience separately before shipping
UI changes. Synthetic checks do not establish a watermark-removal accuracy percentage.

### Failed or immutable releases

Check the failed step in Actions. A transient download failure can be retried
with **Re-run failed jobs**. Version mismatches require fixing the source and
using a new version tag; do not move a published tag. If files were already
attached, reruns refuse to overwrite them; inspect the existing release first.

If repository release immutability is enabled, GitHub locks assets at publication.
Use the manual workflow on the exact version tag first, download the three
verified files, attach them to a **draft** release for that tag, then publish.
The automatic attachment job reports the lock and leaves the release intact.
Do not disable immutability just to get past the check.

GitHub's event rules and release behavior are documented in
[workflow release events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release)
and [GitHub CLI immutable releases](https://cli.github.com/manual/gh_release_create).

Employees download and extract the complete Windows ZIP, then open ClearPage.exe.
The app currently has no automatic updater; installing a new release is manual.


For 2.2, run `python scripts/benchmark_suite.py --output-dir build/benchmark`
before `python scripts/release.py verify`. The verifier refuses missing or failed
benchmark gates. Signing is deferred; build reports explicitly label packages
unsigned. The ZIP and JSON reports are covered by the SHA256 file.
