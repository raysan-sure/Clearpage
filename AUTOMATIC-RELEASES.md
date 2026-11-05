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
   `app_version.py` is the source of the app's version; the helper updates the
   README heading too. It does not commit or publish anything.
3. Review and push the approved code. A push to `main` automatically runs tests,
   rebuilds React, builds the Standard Windows EXE, extracts its ZIP, verifies
   config-tamper rejection, and saves a preview artifact. It does not publish.
4. When approved for customers, create and push a matching tag such as `v2.3.0`.
   The tag must match `app_version.py`. This triggers the production build and
   automatic publication after all checks succeed.
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

The React app checks GitHub shortly after opening. There is also a **Check for
updates** button. Checks request only public release metadata: no PDFs, document
hashes, installation IDs or payment credentials are sent to GitHub. Results are
cached for six hours in the running app; temporary failures for one minute.
These requests run without the document-processing lock. The current app must be
updated once to this notification-enabled build before it can show future notices.

The notice appears only for a newer stable, non-draft release containing a finished,
nonempty ZIP with the expected name and GitHub download URL. It links to that ZIP
and release notes. It never downloads in the background, executes an installer,
patches running files or deletes the profile. Customers close the old app, extract
the new ZIP to a new folder and retain their existing ClearPage profile.

## Render and the website download

Each verified production release includes `ClearPage-Windows-x64.zip`, identical
to its versioned ZIP. All website download buttons point directly to
`https://github.com/raysan-sure/Clearpage/releases/latest/download/ClearPage-Windows-x64.zip`.
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
and run the Windows build/EXE verification. Only a future approved tag push can
prove the GitHub-hosted runner and publication permissions work end to end. No
GitHub publication or live payment is part of the local tests.
