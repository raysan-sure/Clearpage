# ClearPage local download website

## Vercel download site

Deploy this directory only, with Framework Preset **Other**, no install/build
command, and output directory `.`. For a Git-connected project, set Root Directory
to `website`. The Python application and payment service are not deployed here.
`vercel.json` keeps this a static HTML/CSS/JavaScript deployment.

Download buttons check published stable releases in `raysan-sure/Clearpage`.
When no stable release exists, they offer the explicitly approved
`v2.3-preview.1` prerelease. Its verified `ClearPage-Windows-x64.zip` opens the
Windows app, but PDF processing remains locked until payment hosting is configured.
Payments remain disabled. The page labels this preview limitation before download.

To preview with an explicitly built testing ZIP, run from the repository root:

```powershell
.venv/Scripts/python.exe scripts/preview_website.py --archive release-assets/ClearPage-Windows-x64.zip
```

Open `http://127.0.0.1:5173/`. Download buttons serve that ZIP locally and label it
as a preview with payments disabled. This loopback-only helper is separate from
the Render entry point and does not publish or distribute a customer release.
On the hosted website, buttons check GitHub for the uploaded stable Windows ZIP
before navigating. Missing releases and network failures display a message on
the website instead of navigating to a GitHub 404. The first public release is
still required for customer downloads.

Plan: INR 999 for **two** distinct PDF documents. Seller: Raysan sure.
Support: sales@zynape.com.

This is a buildless HTML/CSS site. The local preview is assembled under the task's
`outputs/ClearPage-website` directory. All four download buttons follow the public
GitHub latest-stable-release URL for `ClearPage-Windows-x64.zip`. Each verified
production release publishes this stable-name asset alongside its versioned ZIP.
No website edit or Render ZIP upload is needed for future releases. See
`AUTOMATIC-RELEASES.md`; the first compatible published release is required before
the public download link works. The local ZIP in outputs is available for review.

Nothing is published. Terms/privacy text is a draft. Custom cleanup includes the seller-requested
two-hour delivery-or-refund promise after receipt of complete requirements and any
requested sample. Enquiries prepare an email draft to sales@zynape.com; they do
not send email automatically. Checkout is disabled. Complete seller review, payment hosting,
and policy details before any public paid launch or Razorpay submission.

The locally built package is the Standard preview. It opens in its own Windows
application window using Microsoft Edge WebView2 Runtime, not a browser tab.
PDF contents stay on the customer computer during standard cleanup; online credit
verification uses installation IDs, document fingerprints and payment references.
Custom-support samples are shared only when the customer chooses to send them.
No absolute confidentiality guarantee is made about customer devices or samples.
The package excludes optional
LaMa and neural OCR models/runtimes. To reproduce a local Standard preview in a
clean Windows Python 3.12 environment with application requirements and PyInstaller:

```powershell
npm ci
npm run build
$env:CLEARPAGE_BUILD_STANDARD='1'
$env:CLEARPAGE_BUILD_PREVIEW='1'
python -m PyInstaller --noconfirm --clean ClearPage.spec
```

The preview flag explicitly permits an incomplete public `billing.json` for local
review. A commercial customer build must use complete public payment settings and
omit that flag. Keep the entire `_internal` directory beside the EXE, include
applicable dependency licenses, smoke-test the packaged executable, and ZIP the
whole application folder. Never bundle merchant secrets or seller signing keys.

The current Windows build requires online authorization. See HOSTING.md for the
Render deployment, persistent storage, secrets and build-order requirements.

Live static website: https://clearpage-omega.vercel.app
Vercel project: raysansure-2964/clearpage. Deployed with the CLI.
Automatic Git deployments require a GitHub login connection in this Vercel account.

