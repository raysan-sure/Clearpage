# Standard Windows release automation

These changes are local. No commit, tag, push, release or deployment was performed.

## One-time setup after approving the code

The GitHub repository must be public for the application's unauthenticated release
checks and customer downloads. If it is private, a public update distribution
endpoint is needed; never embed a GitHub access token in the desktop application.

In repository **Settings → Secrets and variables → Actions → Variables**, add:

- `CLEARPAGE_PAYMENT_SERVICE_URL`: your controlled HTTPS payment origin.
- `CLEARPAGE_SIGNING_PUBLIC_KEY`: the seller's public Ed25519 verification key.

These are public customer settings. **Never put Razorpay Key Secret, webhook secret,
private signing PEM or its password into this workflow.** Those stay on Render.
The workflow uses GitHub's built-in temporary token for the publish job only.

## Future pattern updates

1. Add and test the new pattern/preset, including any required packaging assets.
2. Run `python scripts/set_version.py 2.3.0` (use the next unused version).
   `clearpage/app_version.py` is the source of the app's version; the helper updates the
   README heading too. It does not commit or publish anything.
3. Push the approved code to `main`. The workflow chooses a version newer than
   the latest stable release (incrementing its patch when necessary), runs tests,
   rebuilds React and the Windows EXE, and checks the extracted app and updater.
   A successful production build publishes its exact source commit automatically.
   A superseded main build is skipped. Build version changes happen in CI only.
4. Alternatively, push a numeric version tag such as `v2.4.1`, or publish a
   stable GitHub release with that tag. Tagged builds use the tag's version.
   A published empty stable release receives the verified assets; existing assets
   are never replaced. Releases already containing the website ZIP are skipped.
5. The publish job checks the exact artifact checksum and test report, refuses
   preview/older/existing releases, creates a draft, uploads the verified ZIP,
   build report and checksum, plus an identical stable-name website ZIP, then publishes the draft. Publish jobs serialize
   so two finishing builds cannot mark an older release as latest. A partial
   failed draft is retained for owner inspection; reruns do not overwrite it.

Manual **Actions → Windows release → Run workflow** runs preview builds only.
Release builds fail if the public payment configuration is missing or invalid.
The Standard edition omits optional LaMa/ONNX models. The EXE remains unsigned.
It opens in a dedicated Windows window using Microsoft Edge WebView2 Runtime,
which must be installed on Windows 10/11 x64. A missing renderer shows a startup
error instead of launching a browser. Closing the window stops the local service.
Payment checkout and release links still open externally; document work stays
inside the application. File selection and export use the desktop window's dialogs.

## Customer notification

The Windows Standard app checks GitHub shortly after opening and every fifteen
minutes. **Check for updates** requests an immediate check. Checks request only
public release metadata: no PDFs, document
hashes, installation IDs or payment credentials are sent to GitHub. Results are
cached for fifteen minutes in the running app; temporary failures for one minute.
These requests run without the document-processing lock. The current app must be
updated once to this notification-enabled build before it can show future notices.

Only newer stable, non-draft releases with the expected finished Windows ZIP are
eligible. Automatic downloads also require GitHub's SHA-256 asset digest. The
app bounds download/extracted sizes, validates paths and the package manifest,
and checks the checksum before staging. Downloads stay within GitHub's HTTPS
release storage. Older assets without a digest retain a manual download link.

After sixty seconds without interaction, ClearPage waits for processing and
dialogs to finish, checkpoints open documents and page choices locally, and
hands off to its independent `ClearPageUpdater.exe`. **Save and restart now**
can start this sooner. The helper waits for the original process to exit,
replaces only the known application entries, and restarts the same EXE path.
Unrelated files in the app folder and the user's app profile are preserved.
The new app restores the checkpoint. Its WebView2 window must report a loaded
React interface and the expected version; otherwise the helper restores the
previous files and restarts the old version. A failed version is held for 24 hours
unless the user manually retries. The latest successful backup is retained;
older completed updater folders are pruned. A blocked/read-only installation
reports the error and offers the manual download rather than requesting elevation.

Existing installations need this updater-enabled package once before they can
update themselves. Source development runs, private owner-test builds and the
optional AI edition do not replace their own files. The update mechanism is
tested with generated PDFs and simulated release responses, including damaged
downloads, partial installation failure, desktop failure, rollback and resume.
Release publication requires the real bundled helper to pass its install,
desktop restart, document resume and rollback checks.

The first production release still requires the public payment service URL and
public signing key above. Missing configuration fails the build; the pipeline
never silently distributes an inactive preview over a working production app.
Neither the local changes nor a local preview build publish anything to GitHub.

## Render and the website download

Each verified production release includes `ClearPage-Windows-x64.zip`, identical
to its versioned ZIP. All website download buttons point directly to
`https://github.com/invicto-labs/Watermark-remover/releases/latest/download/ClearPage-Windows-x64.zip`.
GitHub resolves that URL to the latest published stable release, so future releases
require no website edits or copying ZIPs to Render. Drafts and preview artifacts
are never published by this workflow; the alias is checked against the same SHA-256
before upload. The previous Render download URL redirects to that same GitHub URL,
with caching disabled. Local stale ZIP files are no longer served by that route.

The website and compatibility route need their first deployment to get this change.
The first new production release must be published before this download link works;
older releases may lack the stable-name asset. Before that, use the local preview ZIP
from outputs for review. GitHub must be public. No GitHub token is sent to customers.
This does not deploy Render; auto-deploy remains off and no deploy hook was added.

## Verification limits

The Standard package includes `ClearPage.exe.config` beside the EXE so its .NET
desktop libraries load after browser download and Explorer extraction retain
Internet-zone marks. Keep this file with the executable and `_internal` folder.
The setting is scoped to the ClearPage process. Packaging checks now mark the
extracted DLLs as downloaded, open the real WebView2 window, wait for the React
interface to render, and close it automatically. Publication requires this check
in addition to the backend and billing checks. Startup failures are recorded in
`%LOCALAPPDATA%/ClearPage/startup-error.log`.

Local checks can validate workflow syntax, unit-test release and update behavior,
and run the Windows build/EXE verification. Only a future approved main/tag push or stable release can
prove the GitHub-hosted runner and publication permissions work end to end. No
GitHub publication or live payment is part of the local tests.
