# ClearPage 2.4.1

A local Windows application for watermark cleanup in PDFs and scanned images.

## Repository layout

Run commands from the repository root. `app.py` starts the desktop app.
The PDF processing code lives in `clearpage/pdf/`, cleanup profiles in
`clearpage/presets/`, image and text restoration in `clearpage/restore/`,
and desktop payment code in `clearpage/billing/`. The seller service lives
in `payment_service/`. See `tests/` for regression tests, `docs/` for guides,
and `config/examples/` for sample billing settings.

## Optional paid document packs

Sell **$99 packs for two distinct PDFs** using Razorpay checkout with
server-verified payments and signed online document authorization. Reopening and
downloading the exact same paid PDF does not use another credit. See
[RAZORPAY.md](docs/RAZORPAY.md) for test keys, the separate seller payment service,
deployment and recovery. Merchant secrets never go into the customer app.
The optional manual bank-transfer provider is documented in [BILLING.md](docs/BILLING.md).
Owner-only login in the desktop [Admin panel](docs/ADMIN.md) authorizes document work
without spending credits. It requires private server provisioning and online
verification; customer builds have no default admin account or free-use switch.

## React interface

The main interface uses React 19, Tailwind CSS 4, and Lucide icons. It includes a
responsive workspace, drag-and-drop PDF and scan upload, visual cleanup presets,
original/cleaned page comparison with zoom, page selection, and PDF export.
All frontend assets are bundled locally; no CDN or external fonts are needed.

To rebuild the interface after changing `frontend/`:

```powershell
npm ci
npm run build
.venv/Scripts/python.exe app.py
```

For development with React Fast Refresh and Tailwind hot reload, run `npm run dev`.
This starts the local PDF backend and Vite together. Open
`http://127.0.0.1:5173/` directly in any browser; no key, login, or launch link is required. Changes to
`frontend/` appear immediately. The backend stays running so PDF sessions survive
frontend edits; restart the command after changing Python code. Ctrl+C stops both
servers. Set `CLEARPAGE_PYTHON` to use a different Python environment if needed.

Vite writes `static/index.html`, `static/ui.js`, and `static/ui.css`. These built
files are included by the existing Windows packaging configuration. End users
do not need Node.js. The normal application opens at `http://127.0.0.1:5055/`.
Documents remain in the current application session;
the interface does not persist customer files in browser storage.

After upload, **Suggested for this file** checks the first, middle, and last
pages locally and shows up to three matching cleanup types, with explanations
and page numbers. Choose **Use this type**, then preview the result. Suggestions
do not change the PDF or select a cleanup automatically. **All cleanup types**
keeps every preset available and opens when no clear match is found. Sampled
pages may not represent the entire file; review other page types before export.

## Lossless PDF compression

After uploading a PDF, choose **Compress PDF** in the top bar. Compression targets
**25,000,000 bytes (25 MB)** while preserving image samples, resolution, text and
page content. The tool uses local pikepdf/qpdf stream and object optimization.
It keeps the smaller of the optimized file and the original bytes and reports
whether the target was reached. Already compressed scans may remain above 25 MB.
The tool never lowers image quality to force the target.

Choose the original upload or a completed cleanup PDF. To compress reviewed cleanup,
download the reviewed PDF first, then choose **Completed cleanup PDF**. Compression
uses the uploaded document's existing authorization and does not spend another credit.
Digital signatures and encrypted PDFs require an unsigned, unlocked copy.

## Light-gray marks on black ink scans

For large diagonal gray **Unofficial Copy** lettering, use **Unofficial copy**.
The preset retains strong matches to existing layouts and automatically aligns
the known lettering on other supported pages, including changes in size/angle
and clipped upper-right edge fragments. Suggestions also inspect native scans
with fully opaque PDF image masks. Weak alignment is rejected. Aligned cleanup
preserves every original dark pixel (levels 0–75) and all pixels outside its
cleanup area; faint strokes at overlaps still need review. No AI or OCR is used.
Preview and export share the same versioned native-image cache.

Choose **Gray watermark · black ink scans** for repeated horizontal “Unofficial Copy /
Gallia County” marks like the tested search package. Preview, clean all pages,
then review and download. This local preset removes only the separate light-gray
range and verifies that every original black pixel remains unchanged. It does
not rewrite text or require AI. It works on original scan samples and preserves
page placement, cropping, rotation, dimensions, and order.

The Gallia detector rejects colored scans, intermediate gray text, non-repeating gray
marks, and unsupported PDF images. Those pages stay unchanged. A matching pixel
palette is not proof that all gray content is a watermark: review before export.

The same option detects the diagonal St. Tammany “UNOFFICIAL” stamp on supported
grayscale scans and recording covers. It removes the separate gray stamp from
the scans and reverses the tint on the covers, preserving black pixels and
colored seals. Matched pages start selected for export. Review the cover text
where the stamp crossed it before downloading.

## Red diagonal scan watermarks

Choose **Red “Not an Official Copy”**, preview a page, then select **Clean all**
and review before downloading. This runs the local color-separation script used
for pale red diagonal marks on black-and-white scans; no API key or AI model is
needed. It does not rewrite words. Faint-ink protection retains neutral/non-red
pixels exactly and excludes detected text regions and nearby letter edges from
background whitening. Ambiguous traces may remain where removal would risk text.
Simple RGB scan PDFs are processed and exported
at their original pixel resolution, with page order and dimensions preserved.
Other layouts use the rendered-page path. Pages without a large diagonal red
pattern are retained. Red document content within the watermark band and faint
overlapping strokes can change, so compare names, numbers and signatures.

## Optional Gemini cloud draft

Choose **Gemini AI draft · cloud**, enter a Gemini API key in the app's password
field, and select **Use key for this session**. Never put keys in chat, source
files or document settings. No local AI package is required. The key is held
only in backend process memory and can be removed with **Forget key**; closing
the backend also clears it. This button does not revoke the key at Google.

Choose a page in the viewer, confirm sending that page to Google, then select
**Create draft of page N**. This explicitly opt-in trial uses
`gemini-3-pro-image` through Google's HTTPS API. Google model access, billing,
quotas and data policies apply. Standard presets stay local. The trial never
runs from automatic detection or batch queues, and never sends the entire PDF.
Other pages keep their originals. A new trial replaces the current review;
successful page drafts are cached for the local document session to avoid paying
again for the same request. Failures are not automatically retried. Stopping
discards a pending result, but cannot cancel billing for an already submitted
Google request.

AI may reconstruct the entire page and change text, signatures or barcodes.
Generated images include Google's SynthID. No accuracy percentage is assigned.
Compare the original and AI draft, mark the page reviewed, then select that
cleaned page for export. Selected AI drafts cannot be exported without review.
The original file remains unchanged, and every page stays in the exported PDF.
Responses with changed page proportions, invalid images or incomplete output
are rejected; accepted images are resized to the source canvas when needed.

Protocol reference: [Google image generation and editing](https://ai.google.dev/gemini-api/docs/generate-content/image-generation).
`tests/test_gemini_cleanup.py` exercises mocked protocol responses, credential secrecy,
explicit consent, cancellation, caching, review enforcement and multipage export.
These tests do not measure Gemini's visual quality or verify live account access.

## Repeated diagonal lettering on scans

The React interface also offers **Repeated diagonal · local AI**, using the
existing LaMa `repeated_diagonal_ai` backend. It checks for the verified model
and local PyTorch runtime before enabling processing. No Gemini key or cloud
request is needed. Preview the first page, then generate drafts for the file.
Changed AI pages start unchecked; compare each page, check **Mark page reviewed**,
and select the drafts you accept before downloading. Both the browser and
backend enforce review. A Stop control is available during processing.

This uses the earlier LaMa model and its full diagonal-band cleanup coverage.
After inference, a separate pass copies supported source strokes back into
the draft. It learns both watermark ink and whitespace from blank-area copies;
at least two copies must cover each restored pixel. A restored pixel must lie
inside a document-content region, be substantially darker than every nearby
reference, and not be an isolated watermark donor. Faint strokes and antialiased
edges are also copied when at least three independent references all have a
white 3-by-3 neighborhood (every reference pixel is at least 245/255). This
additional rule applies only to original gray levels 100–229; a dark reference
neighbor vetoes it. Unknown areas retain the
full AI cleanup instead of being skipped, fixing the narrow-mask regression.
No words are retyped or inferred by the copy-back step. Pixels outside that
step match the earlier AI result. Predictions use a versioned cache; both
outside-mask preservation and copied source strokes are checked when loading.
Opaque intersections can still lose letters, and watermark fragments can remain.

This does not reproduce the separately uploaded
"ChatGPT Image" reference. It can remove covered letter strokes and does not
guarantee 100% watermark removal or correct wording. Standard offline cleanup
remains available as **Repeated diagonal** and does not require PyTorch.

Upload a PDF, JPG, PNG or TIFF and select **Repeated diagonal**. The React
interface now calls the `repeated_document` method. It detects regularly spaced
diagonal bands, finds horizontal document rows using ink outside those bands,
and removes isolated watermark components in margins and text gaps. Short
recording numbers, row ends, connected handwriting, stamp interiors and barcode
strokes are guarded. No AI dependency, OCR or generated text is used.

An additional offline pass learns the repeat spacing from at least three
independently matched blank-area patches. It compares multiple intact copies
of each predicted watermark pixel, rejects inconsistent matches, and removes
qualifying lettering around protected document strokes. Dark ink, faint text
rows, tall signature strokes and barcode structures remain guarded. The
`template_extra_pixels` statistic measures additional edited pixels, not
accuracy. Unmatched patterns fall back to the conservative pass.

The same algorithm is available as a standalone script (using the application's
normal Python dependencies, without Gemini, AI weights or OCR):

```powershell
python clean_repeated.py "input.jpg" "output.png"
python clean_repeated.py "input.pdf" "output.pdf"
```

The script refuses to overwrite inputs or existing files, writes a JSON report
to standard output, and retains every PDF page. Image outputs are lossless PNGs.
It cannot recover original strokes that an opaque watermark has destroyed.

Try the first page, compare the result, then clean all pages and select the
versions you have checked. Scans are exported as PDFs. The preview and batch
result show the method's limitations; changed pages start unchecked. This is
**partial cleanup**: opaque watermark pixels merged with document ink cannot be
recovered exactly from a flattened scan. Marks in protected areas may remain.
Changed-pixel counts and residual ink counts are not accuracy percentages.

`tests/test_repeated_document.py` checks known text (including faint text and short
numbers), signatures, stamps and barcodes at three image sizes, clean-page
rejection, cancellation, non-AI routing, image upload, and preview/export parity.
The older `repeated_diagonal` API remains available for margins-only cleanup.

## Huntington watermark - step by step

Under **PDF type**, select **Huntington watermark - step by step** for CH search
packages with the diagonal IMAGES / COPY PROPERTY Huntington County phrase.

1. **Try first page** matches the known phrase and removes separable background ink.
2. A second pass uses intact neighbouring words to establish text-line gaps. OCR
   supplies positions, not replacement wording. If Windows OCR is unavailable,
   background cleanup still works and reports the skipped refinement.
3. The passes repeat against fixed source guards until nothing else qualifies.
   **Huntington: cleanup steps and text review** reports each stage and its changed-pixel count. Convergence
   means no further safe change was found; it does not mean every watermark is gone.
4. **Clean all pages**, then use **Find matching words** in the Huntington panel to find matching scanned
   words. Huntington donor matching requires agreeing source/preview readings,
   matching visible strokes, and adequate surviving ink. It excludes digits and
   proper names and changes only pixels inside the matched watermark corridor.
5. Review the remaining OCR hints together, in groups of 40 across all pages. Click
   one to see zoomed original/current crops. For a damaged word, confirm its exact wording,
   preview a clean scanned occurrence or a fitted font, then apply the repair.
   Accepted repairs survive another cleanup pass and can be undone.
6. Select reviewed pages and create the PDF. Every original page remains included.

This separate preset strictly matches the included Huntington phrase, clears
watermark ink between horizontal text rows, and protects local faint text, small
punctuation, vertical rules and tall graphic strokes. It avoids retaining a whole
diagonal watermark word just because that word touches one document letter. The
background stage requires no AI, OCR, painting, or replacement words. Unmatched pages remain unchanged; changed
pages require selection. Export preserves every page and its geometry, adding
sparse visual corrections using the existing image export path. Original scan
objects remain underneath the corrections. This is partial visual cleanup:
watermark ink merged with protected text may remain, and a 100% original or
watermark-free result cannot be guaranteed from a flattened scan.

## Preble County - Unofficial Copy

Select **Preble County · Unofficial Copy** for the fine-line diagonal watermark
in Preble County search packages. Preview, clean all pages, then review and export.
This is a separate preset from the gray and dotted watermark types.

The preset matches a watermark-only silhouette and removes fine pattern ink at
the original scan resolution, protecting solid strokes and long rules. It supports
the verified 2300-2800-pixel-wide, 1-bit DeviceGray CCITT/Flate portrait scans,
including centered page insets. Unsupported structures or unmatched pages remain
unchanged. Export retains all pages, their geometry, drawing streams and native
image dimensions; unchecked pages retain their original images. No OCR or
generated text is used. Inspect fine characters and intersections: attached
watermark fragments may remain, and very thin overlapping ink is ambiguous.

Regression coverage is in `tests/test_preble_scans.py`. The packaged pattern contains
only the watermark silhouette; customer documents are not bundled.

The edge refinement uses a centered solid-stroke guard and nearby horizontal
serifs instead of retaining a full fringe around every letter. It reduces attached
halftone marks such as those beneath "Situated" and beside "97". Reprocess pages
to use the refinement; earlier native preview caches are ignored. Fine overlapping
ink remains ambiguous, so this is not a guarantee of pixel-perfect text recovery.

## Nassau County - pale lettering and tiled border

Select **Nassau · Unofficial Copy** for pale diagonal outline lettering inside a
repeating square border, then preview, clean, compare and select pages to export.
For the known Nassau layout, the preset compares the printed letters and frame
with nearby paper, including on small scans and densely printed pages. Other
layouts still require both border repetition and a diagonal band of pale ink.
Matched pages start selected for export and can be compared or deselected.
Blank, unmatched and unsupported pages stay original, including pages mixed into
the same search package. Only simple, unrotated, full-page RGB portrait scans are
supported. Native image resolution, page geometry and all pages are preserved.

This preset lightens neutral pale pixels in the detected diagonal region and
border while retaining dark ink. Faint gray document
content inside those regions can also be affected: review light text, signatures,
numbers and intersections before selecting the result. No OCR, invented text or
AI reconstruction is used. The example is synthetic. The detector templates
contain only the watermark design and an isolated geometric tile.

## Gray watermark cleanup

The **Unofficial Copy** preset also clears pale compression speckles left around
the matched gray letters after reverse blending. It also removes pale connected
fragments that trace the known watermark outline; dark ink, crossing document
strokes and pixels outside the matched watermark remain protected.
Reprocess previously cleaned pages to apply it; older native gray caches are
ignored. Review small symbols and overlapping text before exporting.

## Dotted scan cleanup

Under **More options > PDF type**, choose **Dotted watermark · search package**
for the centered diagonal dotted "Unofficial Document" layout on portrait scans,
or **Dotted watermark · plat map** for the large landscape map layout. Try one
page, clean all pages, review the result and select the cleaned pages to save.
Pages flagged for review start unchecked for isolated-dot cleanup. All pages
remain in the exported document.

These two presets remove dense clusters of isolated 1–4 pixel dots in their
respective diagonal regions. Connected text and map strokes are retained;
dots joined to those strokes can remain. Small isolated punctuation or symbols
inside the selected area still need review. These are layout-specific presets,
not automatic recognition of arbitrary watermarks or a text accuracy guarantee.

Isolated-dot cleanup supports simple full-page, unrotated, 1-bit Indexed
black-and-white PDF scans encoded with CCITT or Flate. Unsupported image structures stay
unchanged. Large supported maps use bounded previews, while native cleanup and
lossless export preserve the image dimensions, page geometry and drawing stream.
Limits: 150 million native pixels and 24 million pixels in the processing region.
No OCR, AI word replacement or whole-page resampling is used for these presets.
They cannot be combined with color inversion in the same pass.

**Dotted document** also supports the 1500 × 1800 grayscale search-package
layout with the diagonal “Unofficial Document” mark. ClearPage learns the
watermark from matching pages in the upload. This route needs at least three
pages from each of three supported reference variants and leaves pages without
enough matching evidence as they were. Matched cleaned pages start selected
for export and can be deselected individually. It preserves pixels outside the
detected mark. Review overlapping text and signatures because pixels obscured
by the watermark are estimated from nearby strokes.

Regression coverage is in `tests/test_dotted_scans.py`, automatically included by the
existing Windows CI test discovery. Company sample PDFs are kept out of Git.

## New in 2.2

- **Select a text watermark:** find isolated text objects on the current page,
  click the orange area or choose its wording, and optionally match the same
  appearance on other pages. Selection replaces the previous selection. Preview
  and process using Selected text watermark; changed pages start unchecked.
  Export and saved workspaces preserve the selection. Clear text selection
  removes it without changing the original. Scanned marks, mixed body-text blocks,
  embedded/custom fonts and complex graphics are intentionally not offered.
- **A-PDF variants:** the validated A-PDF drawing grammar now supports standard
  Helvetica/Times/Courier styles, RGB colors, 5–85 degree rotations, font sizes
  18–240, and opacity 0.05–0.85. Isolated SAMPLE/DRAFT/COPY/CONFIDENTIAL or repeated
  identical text can match. This is not generic removal of diagonal body text.
- **Faint scan text:** Careful cleanup protects connected ink through gray level
  224. Potential text overlaps are flagged and affected results start unchecked.
  This can leave more watermark residue; completely covered letters remain uncertain.
- **Benchmarking:** separate corrected-watermark pixel, faint-ink match and
  unintended-change metrics. A deterministic 11-page, 5-category synthetic suite
  runs in CI; private benchmarks can fail on regression or missing coverage.
  These are pixel measurements, not measured accuracy on company documents.
- **Guided updates:** release version, package name, plain-text notes and steps
  for saving, verifying the checksum, extracting separately and keeping a rollback.
  Signing is deferred. The package is unsigned; checksums are not signatures.

## New in 2.1

The supplied A-PDF watermark styles are now detected automatically on upload:
repeated pink `www.apdf.com` text and the large blue diagonal `Sample` mark.
The app selects **PDF text / website watermark**, previews the removed PDF
objects, and exports selected pages using the same object-removal path. Body
text, fonts and images remain native PDF content; no OCR or repainting is used.
The detector checks the complete isolated drawing block, standard font mapping,
color, transparency, rotation and text. Other styles or ambiguous blocks remain
unchanged. This does not add generic removal of all diagonal or repeated text.

## New in 2.0

- **File queue:** open up to 20 PDFs/scans (200 MB per input, 500 MB total).
  Each file retains its own cleanup settings. Clean waiting files sequentially;
  stop the queue without changing the original input files.
- **Review before ZIP:** compare each changed page, choose whether to keep its
  original or cleaned appearance, and mark it reviewed. Finish the file review
  to include it in **Create reviewed ZIP**. Only finished reviews are included;
  each output PDF retains every page. Duplicate input names get numbered outputs.
- **Save and resume:** **Save work** or **Save & close** writes a local checkpoint
  containing the input copies, previews, page choices, accepted word repairs,
  undo history and PureScan model. Reopen the app and choose **Resume**. Save again
  after further edits; closing the browser alone does not create a checkpoint.
  Up to five workspaces, each up to 2 GB uncompressed, can be saved. A failed save
  keeps the previous checkpoint. These local ZIPs are not encrypted and should
  be treated as company documents. Delete saved copies from the welcome screen.
- **Review tools:** synchronized zoom and scrolling, highlighting of actual
  changed pixels, page review labels, and a next-page-to-review button that
  prioritizes uncertain corrections. Highlighting is never added to exports.
- **Saved setups:** save named cleanup settings, including a custom painted
  area, for reuse. Per-page inversion overrides are not transferred. This is
  settings reuse, not a newly trained detector; additional county templates
  still require representative samples and validation.
- **Updates:** click **Check for updates** to query the public Invicto GitHub
  release metadata. The app offers the release page only when a newer stable
  Windows ZIP is available. It never uploads documents or installs updates.

Batch defaults use the existing Careful cleanup with automatic word restoration
off; enable it per file when needed. The underlying removal models are unchanged.
This release adds workflow and review controls, not a new accuracy percentage.

## New in 1.9

Open **PDF, JPG/JPEG, PNG, or TIFF** directly. Every page of a multipage TIFF is
imported in order. Images become a local PDF container; exports are always PDFs.
The original uploaded file is retained unchanged for the session. Decoded RGB
pixels are embedded without an additional JPEG compression pass. EXIF orientation
is applied, transparent areas become white, and usable DPI metadata determines
page size, rounded to the nearest half point to align exported corrections with
the preview grid. Missing or invalid DPI uses 144 DPI. Processing previews still use
144 DPI, so changed areas are not a full-resolution image-editing workflow.

Under **More options**, choose the watermark type and enable **Fix black pages
before watermark cleanup**. Detected negative scans are corrected before image
cleanup, OCR, donor matching, or PureScan learning. In website-overlay mode,
recognized overlays are removed before the scan colors are corrected. These
operations now share one preview/process/export workflow. Normal pages retain
their colors; uncertain negative scans can use the per-page inversion override.
All color-corrected pages require review and selection for export. The Before
preview remains the unmodified source. To correct colors only, select the
existing **Black page → white paper** mode instead.

The app keeps OCR and PureScan caches separate when colors change. Existing
word-repair patches cannot be reused with different color settings: keep the
previous setting, or save your work and reopen the original before changing it.
This avoids silently discarding or incorrectly recoloring accepted repairs.

Image imports allow up to 200 MB, 300 TIFF pages, 12 million decoded pixels per
page, and 120 million pixels per image file. A low DPI value that would exceed
the existing PDF render limits is rejected. Animated PNGs and other image
formats are not supported. A damaged or oversized import leaves no partial job.

## New in 1.8

**Black page → white paper** is available under More options / PDF type. It
analyzes each page for a negative scan and previews an exact RGB inversion at
144 DPI, without OCR or retyping. Normal and uncertain pages remain unchanged.
Use **Invert this page even if not detected** for a missed page. All proposed
inversions are unchecked for export until reviewed. Inversion reverses every
color, including photographs and stamps; standalone inversion does not remove watermarks. For the combined workflow,
choose a watermark type and enable the new color-correction checkbox.
The complete PDF retains page order, dimensions, crop and rotation; unselected
pages stay original. This is a rendered correction, not lossless restoration of
an original higher-resolution image. Existing word repairs are bypassed in this
mode and retained for image-cleanup modes.

**Quality check** compares the current processed preview with a trusted clean
PDF and exports a JSON report. For repeatable private benchmarks across releases,
see [BENCHMARK.md](docs/BENCHMARK.md) and benchmark.py. These measure pixel differences
and preservation; they do not claim word accuracy or an overall percentage.

## Employees: start here

### Dotted Un-Official scans

Choose **Dotted Un-Official** when suggested for a diagonal dotted mark like
the supported 1728 × 2160 scan. **Dotted document** also routes this detected
pattern to the same cleanup. Single-image scans and two equal grayscale image
bands are supported; other patterns and sizes are left unchanged by this preset.

This local script removes the periodic screen and reconstructs damaged strokes
at the native resolution. It uses the same filters as the refined restoration,
preserves every observed non-watermark black pixel and every pixel outside the
watermark area, and exports the original image placement without rasterizing
the PDF. Four previously reviewed common-word patches are applied only to an
exact matching native-page fingerprint. They cannot substitute words in another
document. No AI runtime, API key, or OCR dependency is required for this preset.

Reconstruction is approximate: removal of a visible pattern does not establish
100% word or signature accuracy. Review and select the cleaned pages before
export. Page selection, preview and PDF download use the same native results.

The packaged `assets/screened-unofficial.npz` contains the watermark pattern,
small numerical filters, four common-word patches and their page fingerprint;
it does not contain the original PDF, names, signatures, or complete pages.

### Basic workflow

1. Extract **the entire ClearPage-2.2-Windows-x64.zip** to a new folder on your computer.
2. Open the ClearPage folder and double-click **ClearPage.exe**. Keep the `_internal` folder beside the application.
3. ClearPage opens in its own Windows desktop window. PDF contents are processed on your computer. The paid Standard edition requires internet to verify credits and document access. Python is bundled; Windows 10/11 x64 and Microsoft Edge WebView2 Runtime are required.
4. Click **Choose a PDF or scan**, or drag a supported file onto the upload box. Recognized website overlays use the same automatic selection as before. Other PDF types can be selected under **More options**.
5. Click **Try this page** for a preview, or **Clean all pages** to process the document. Compare **Before** and **After**. Use the page arrows and **Zoom in** to inspect the text.
6. Keep **Use this cleaned page** checked for pages you accept. Uncheck it to keep the original page. AI and uncertain results still keep the original by default. **Choose which pages to clean** provides the complete page list.
7. Click **Create PDF**, then **Download PDF**. All pages are included in their original order and with their original dimensions.
8. Finish exporting before closing the desktop window. Closing it stops the local app service and cancels unfinished work; keep your app profile to preserve account access.

### Simplified interface update (27 October 2026)

The main workflow is Open PDF, Clean & check, Save PDF. PDF-type settings, image-cleanup methods and saved repairs are under More options. Pixel statistics are under Page details; word-repair features are under Word repair tools. Processing, model files, default parameters, page-selection rules and export logic are unchanged from 1.7. Later technical sections retain some original control labels for reference.

More options includes a short document-to-setting guide and examples that update with the selected PDF type and cleanup method. The extra checkboxes, page selection and word-repair methods explain when to use them with concrete examples. Disabled website-mode controls explain why they are unnecessary. This guidance does not change processing settings.

The application is unsigned. If company device policy blocks it, have your IT team review the included source and approve or sign their own build. Do not bypass company device policy.

## What the cleanup does

- **Website PDF overlays:** removes recognized PDF text/vector overlays while retaining the exact embedded scan data. Cook County text blocks and the verified OKCountyRecords vector template are supported. The website need not be open; download the PDF and open it in ClearPage. This is not direct website browsing or universal site support. Unknown or changed templates stay unchanged in this mode. Preview and review every page; unchecked pages remain original. Image cleanup, saved repairs and OCR reconstruction are bypassed in this mode, including when requested through the API.
- **Huntington County diagonal:** matches the recurring “IMAGES/COPY PROPERTY…” watermark used in the original workflow. Uncertain detections appear as preview candidates, but are unchecked for export by default. Inspect their selected pixels before accepting them. It is not a universal watermark detector.
- **Custom area:** applies your brush selection at the same relative position on every page. Best for isolated marks over a blank background. Different layouts require careful review; exclude pages where the selection is inappropriate.
- **Careful:** preserves detected horizontal text and connected dark strokes. It may leave parts of the watermark.
- **Light:** protects dark pixels and nearby edges; only lighter marks are eligible for cleanup.
- The old **Strong** option has been removed because interpolating through text can damage letters. Legacy Strong API requests now use Careful text protection.

Careful and Light cleanup protect detected strokes. LaMa is a generative image model and can invent or omit letter strokes; it does not verify wording. Marks baked over letters can permanently hide information; perfect restoration is not guaranteed. Inspect important text and numbers against your original before relying on the result.

## Website PDF overlays (new in 1.7)

The scan remains at its original resolution, without recompression or retyping. Export edits the PDF drawing commands instead of covering the watermark with a new raster layer. Page count, geometry, rotation and order are preserved. Existing image-mode word repairs are retained in the session but do not apply to overlay-mode output. Password-free opening of PDFs with permissions encryption is supported; files requiring a password are still rejected.

Recognition is deliberately narrow: Cook County requires both exact watermark phrases in isolated Helvetica text blocks beside a scanned image. OKCountyRecords uses a fingerprint of the entire verified vector overlay and its resources, not merely a matching resource name. New website templates need additional inspection and validation before support can be claimed. This mode cannot separate a watermark already baked into scan pixels.

Validation covered all 24 pages in the supplied 22-page Cook County and 2-page Harper examples. Every embedded image stream remained byte-for-byte identical. All output pages matched independently prepared scan-only renders. Six additional synthetic regression tests cover shared forms, unchecked pages, crop/rotation preservation, unknown templates, password handling, cancellation, preview/export and reconstruction guards. The previous 32 tests also pass. Company PDFs are not distributed in the application package.

Open the local app URL directly in any browser. No session key or login cookie is required.

## Text recovery (added in 1.6)

The Windows package adds PaddleOCR's PP-OCRv6 recognition model through a small RapidOCR-based ONNX adapter. It compares the original word with the cleaned word locally. No images or recognized text are uploaded, and no GPU or download is needed at runtime. The 21 MB model is hash-verified before loading.

With **Restore matching words automatically** enabled, clean scanned-word copies are tried first. The new pass then searches Times, Arial and Courier variants, letter dimensions, position and edge weight against surviving original ink outside the watermark. It can reconstruct an entire word rectangle where the existing cleanup left damaged strokes. This replaces pixels inside the word rectangle, including pixels outside the watermark mask; the rest of the page is preserved.

Automatic font fits require an OCR agreement, strong surviving-shape evidence and enough visible ink. Changed spellings must also appear among intact words in the document. Short words, uncertain readings, numbers and unrecognized proper names remain for review. These rules reduce mistakes but do not prove that wording is correct. Shape scores and OCR confidence are not a document-accuracy percentage.

For a stubborn word, expand **Repair damaged words**, select its rectangle, and click **Read original & cleaned word**. This displays both unverified OCR readings without modifying the document or filling in confirmed wording. After checking the wording, choose **Fit confirmed text to original letters** and preview. A fit is refused if too little matching source ink remains. Clean-occurrence copying and typed repair remain available. Apply/undo and the downloadable repair log cover font fits too.

Automatic fits can be undone together using **Undo automatic repairs**; manual confirmed repairs remain. Remaining-overlap suggestions may offer a fitted spelling for review. Reprocessing retains accepted word repairs. A model cannot recover exact information that was completely covered by an opaque mark. This update has not demonstrated 88–95% accuracy on the company document; a clean reference is needed to measure that target.

Developers: install `requirements-ai.txt`, run both `python download_model.py` and `python download_text_model.py`, then `python app.py`. The source ZIP excludes both model files; the employee package includes both. See `vendor/README-RapidOCR.txt` and the included upstream licenses for exact model identity and attribution.

## Local LaMa AI restoration (new in 1.5)

The Windows AI package includes CPU PyTorch and the verified 206 MB LaMa model. No account, GPU, Python installation or network connection is needed. Extract the complete ZIP; do not run the executable inside the archive.

Choose **LaMa AI · reconstruct masked pixels** under Cleanup strength. Preview a page first, then use Process all pages if useful. It uses the selected Huntington mask or painted custom mask, starts from the original scan, and bypasses imported reviewed-page profiles for this mode. PureScan and LaMa are separate alternatives; choose one per pass. Automatic matching-word restoration and confirmed word repairs can still be applied afterward.

AI pages are unchecked for export initially because reconstructed strokes can alter wording. Inspect the original and preview at 100%, select the pages you accept, then Build complete PDF. Unselected pages remain in the PDF as originals. Several minutes may be needed for large documents on CPU; Stop takes effect between model regions or pages.

For one damaged word, expand **Repair damaged words**, select the word, enter its verified wording, and choose **LaMa AI · preview reconstructed strokes**. Preview, check the letters and surrounding text, then apply. The wording field is a review reference; it does not instruct the model to spell that word. Only the watermark-mask pixels within the word selection change. Clean-occurrence copying and rendering confirmed text remain available when the AI preview is inaccurate. Undo last word repair also works for AI repairs.

The model is pretrained LaMa, not a newly trained document model. It uses full preview-resolution tiles and copies only selected pixels, preserving every pixel outside the mask. Original files are never overwritten. Model identity is checked by SHA-256 before loading. See docs/GITHUB-COMPARISON.md for measured results and vendor/AI-PROVENANCE.txt for sources and licenses.

Developers: use Python 3.12 on Windows, install `requirements-ai.txt`, then run `python download_model.py` once to retrieve the pinned weights. Run `python app.py`. The source ZIP omits weights and runtime binaries; the Windows package includes them.

## Blended watermark reduction with PureScan (new in 1.4)

Enable **Reduce blended remnants · PureScan**, then **Process all pages** using the Huntington preset and the original watermarked PDF. The first pass learns a template from up to 40 confidently aligned pages; at least six matching samples are required. Subsequent individual previews can reuse that template. If too few matching pages exist, the app retains Careful cleanup and explains the fallback on each page. Custom painted masks are not supported by this feature.

This integrates the unchanged MIT-licensed mathematical core from [Purestone/PureScan](https://github.com/Purestone/purescan/tree/d1e020828133e266238d976dfcb419dbd6778942), pinned to that commit. Its multiply-blend inversion is surrounded by ClearPage alignment and preservation checks. The full upstream PDF processor is not bundled: it assumes a compatible first RGB image on each page, whereas ClearPage renders tiled scans using its existing PDFium pipeline.

The new pass only touches the known watermark region, keeps previously changed pixels, protects near-black strokes (channel minimum <=32), and skips near-opaque template pixels (minimum <64). These choices intentionally leave unsupported remnants. It does not recover hidden letters or verify wording. Automatic and confirmed word repairs are applied afterward. Turn the checkbox off and reprocess to compare without inversion; already applied word repairs remain.

The preview reports how many pixels the inversion pass changed before word restoration. The feature is opt-in and does not claim lossless restoration of real scans. The source and Windows distribution include PureScan's MIT license and pinned provenance. No new network calls, models, or GPU requirements are introduced.

Exports and the page title identify **ClearPage 2.0** so results can be distinguished from older app windows.

## Automatic word restoration (introduced in 1.3)

**Process all pages** runs automatic restoration when the sidebar checkbox is enabled. For a document already processed, click **Restore matching words on all pages**. The app reads the original and cleaned page images with local Windows OCR, indexes intact word samples, and compares visible letter shapes outside the watermark. It copies scanned words or short phrases only when both wording and image alignment match the acceptance criteria. Common capitalized terms are supported; numeric strings and unrecognized proper names are excluded from automatic replacement.

Similar-looking matches with different OCR wording, capitalization or punctuation are **not applied automatically**. Expand **Inspect remaining overlaps on this page** and click a suggestion to open its preselected repair area and clean source occurrence. Preview it and verify the wording before applying. Other listed overlaps are OCR readings, not verified text; some are already legible. The list is a review aid, not proof that every damaged character has been detected.

A separate pass clears larger isolated marks in blank space outside complete recognized text-line bands along the detected watermark path. It protects complete text-line bands (including words OCR may have missed), OCR word bounds, connected document strokes and small punctuation. Numbers, signatures, diagrams, faint printing and undetected marks still need visual review.

**Undo automatic repairs** removes the automatic word and background changes while retaining manual repairs. Automatic analysis stages its findings before applying them; stopping during analysis retains the existing cleanup. Reprocessing retains applied automatic and manual repairs. To start over, undo automatic repairs and then use per-page manual Undo as needed, or clear and reopen the document.

There is no guarantee of complete watermark removal or exact recovery of hidden text. Match scores measure image similarity, not factual certainty. Save and inspect the completed PDF before relying on it. Automatic repairs stay in the current document session and are included in the repair log; they are not silently added to the persistent reviewed-page library.

## Repair individual damaged words

After **Process all pages**, expand **Repair damaged words**. Use **Select damaged word** and drag a tight rectangle on the cleaned/right page, with a small margin around every letter. Zoom 100% helps select precisely.

Enter the exact wording you have checked. Choose one of two methods:

- **Copy a clean occurrence** preserves the original scanned font and texture. Choose a source page, then **Find matching word**. Windows OCR returns exact, case-sensitive matches outside the detected watermark. Select a thumbnail. If OCR misses a word, use **Select clean word manually** and draw around a clear occurrence on the original/left page. Match the spelling, punctuation, capitalization and style yourself. The selected source word is fitted into the target box; similarly sized selections give the most natural result.
- **Render confirmed text** draws your wording with a local Windows font. Choose Times, Arial or Courier and adjust ink strength. The text fits inside your selection. This supports English text and is a reconstructed facsimile, not recovered original glyphs.

Click **Preview word repair**, inspect the enlarged before/after crops and their surrounding text, confirm, then **Apply this word repair**. Preview does not change the page. Only the selected rectangle changes when applied. Use **Undo last word repair** to step back on the current page. Reprocessing this document retains your applied repairs. Rebuild the complete PDF after edits. Unchecking a page still exports its original, including original words.

**Save repair log** downloads the wording, page numbers and rectangles for applied word repairs. **Save work** and **Save & close** preserve accepted repairs and undo history in a local checkpoint. Removing an open file does not delete an earlier saved checkpoint. These repairs are not automatically added to the reviewed-page library. Importing a reviewed PDF still requires all differences to fall inside the allowed watermark region.

The finder requires a locally installed Windows OCR language and permission to run the bundled PowerShell helper. It never changes execution policy. If your device blocks OCR, manual source selection and typed reconstruction remain available. Windows fonts are loaded from the employee’s computer, not redistributed. No document wording is sent online or silently inferred.

## Reuse a reviewed copy

If you have already checked and corrected a PDF, open its **original watermarked version**, then click **Add a reviewed PDF copy** and select the corrected version. The pages must be in the same order and have the same dimensions. Import currently supports the Huntington watermark preset and rejects changes outside its detected area. It validates where changes occur, not whether the words in the reviewed copy are correct.

After import, click **Process all pages**. Previously reviewed corrections are reused only when the complete source page has the exact same rendered pixels, verified by SHA-256. A changed page, different record, different scan, or shifted watermark will not receive those repairs. Identical pages can be recognized even inside another PDF or in a different page order. Turn off **Reuse reviewed repairs** to use automatic cleanup alone.

This is a local repair library, not a general model trained to recover hidden text. A reviewed source can still contain mistakes. The interface identifies pages restored from your imported copy separately from automatically cleaned pages.

This is **visual cleanup, not secure redaction**. It adds visible corrections while retaining the underlying PDF objects. Original watermark pixels may still be recoverable from the file. It preserves the original as a separate file and never overwrites it.

## Supported input

- Windows x64 build; browser interface tested locally.
- Unlocked PDFs, up to 200 MB and 300 pages, with each rendered page up to 12 million pixels.
- Raster scans are the intended input. Website mode also removes the specifically recognized text/vector overlays described above; arbitrary native PDF overlays are not supported.
- Interactive forms are rejected. Do not use it to preserve or validate digital signatures. Any PDF edit can invalidate a signature.
- Pages with rotation and crop boxes are supported. Source page size, crop, rotation, order, and count are preserved.
- Scans are processed at 144 DPI; pixels outside the correction mask remain in the original PDF objects without recompression.

## Privacy and lifecycle

Document processing is local. There is no telemetry. The application checks for updates shortly after opening, and **Check for updates** also requests public release metadata from GitHub; it sends no documents, filenames or repair text. The app binds only to `127.0.0.1`, checks the connection address and host, and rejects cross-site requests. No session key or login cookie is required. This is a single-user application, not a network server.

Working copies are stored in a randomly named `ClearPage-...` directory under your Windows temporary folder. **Remove this file** removes its open working copy. **Save & close** saves a checkpoint, removes the temporary session directory and stops the server. Checkpoints persist under `%LOCALAPPDATA%\ClearPage\saved-work`; named settings are in `cleanup-presets.json` beside that folder. Delete saved workspaces separately from the welcome screen. Downloaded PDFs remain where you save them. Abrupt termination can leave temporary files behind; these can be removed from `%TEMP%` once ClearPage is closed. Files are deleted normally, not securely erased. Keep company documents in an approved local folder. Developers can set `CLEARPAGE_DATA_DIR` to isolate test checkpoints and settings.

Imported reviewed repairs persist under `%LOCALAPPDATA%\ClearPage\reviewed-pages`. They contain small corrected image regions that may include document text. They are not uploaded or included in the distributable application ZIP. To forget them, close ClearPage and delete that folder. Each employee maintains their own library; follow company rules when sharing reviewed documents.

## For company IT / source installation

This repository contains the application source. For the current paid Standard Windows edition, use [AUTOMATIC-RELEASES.md](docs/AUTOMATIC-RELEASES.md): pushes to main run preview builds; matching version-tag pushes publish verified production releases. The older full-model build instructions below and [RELEASING.md](docs/RELEASING.md) describe the optional AI edition. Use Python 3.12 x64:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
.\.venv\Scripts\python.exe download_model.py
.\.venv\Scripts\python.exe download_text_model.py
.\.venv\Scripts\python.exe app.py
```

To build and verify the portable Windows package from a fresh checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -v -p 'test_*.py'
.\.venv\Scripts\python.exe scripts/release.py build
.\.venv\Scripts\python.exe scripts/release.py verify
```

The verified ZIP, SHA-256 checksums and build report are in `release-assets/`.
The current **Windows release** workflow builds the Standard edition, not this
optional AI package. Use **Actions → Windows release → Run workflow** for a
preview without publishing. See [AUTOMATIC-RELEASES.md](docs/AUTOMATIC-RELEASES.md)
for public payment configuration, automatic releases, and automatic customer updates.

Share the complete ZIP, including `_internal` and `ClearPageUpdater.exe`. The
Standard app verifies future stable updates, saves work and restarts automatically
after a minute without interaction, with rollback if startup fails. Existing
users need the updater-enabled package once. Code signing and a managed installer
are not included. The optional AI edition does not replace itself. The source and application
contain no company document or repaired document text. The watermark preset
contains only the repeating watermark pattern.

## Verification

### Additional scan presets

Choose the matching **PDF type** under **More options**, try one page, then process
all pages. These presets connect to preview, change highlighting, page review,
saved work, and selective PDF export. Changed pages require explicit selection.

| Pattern | App option | Scope |
| --- | --- | --- |
| Dotted diagonal Unofficial Document in a search package | Dotted watermark · search package | Removes supported isolated dots at native scan resolution; connected strokes remain. |
| Dotted diagonal Unofficial Document on a large plat map | Dotted watermark · plat map | Uses the supported map layout and retains native map resolution. |
| Gray diagonal Unofficial Copy on Warren County scans | Gray “Unofficial Copy” scan | Matches supported letter/legal and landscape plat-map scan templates, then reverses the gray blend. Simple full-page RGB scans are cleaned and exported at native resolution. Near-black original pixels stay unchanged. Other layouts are retained. |
| Small repeated diagonal lettering across a scan | Repeated diagonal scan · blank areas only | **Partial cleanup only.** Detects regularly spaced diagonal lettering and removes isolated components outside a conservative document-content rectangle. Overlapping marks inside that rectangle remain. |
| Repeated diagonal lettering overlapping document text | Repeated diagonal scan · AI draft | Full-band local LaMa reconstruction followed by copying supported dark source strokes back into the draft. Opaque intersections remain unverified; some lettering can be damaged or remain watermarked. |

For **AI draft**, try one page before processing the file. Compare the Before and
After views and highlight changes. Select only drafts you accept, then click
**Mark page reviewed** on each selected page before **Create PDF**. The server
rejects exports of selected AI drafts without that review state; original pages
can always be retained. Reprocessing resets review. The model runs locally using
the existing verified LaMa weights, caches predictions per source and mask, and
preserves pixels outside the selected mask exactly. Missing weights, cancellation,
and invalid output do not publish a new draft. This is not the image-generation
service used in chat and does not guarantee complete removal or correct text.

The eight gray-pattern NPZ assets contain watermark templates only. They are
bundled with Windows releases and exercised by the packaged self-test. No source
documents are included. These modes disable generated word repairs and color
inversion for the pass. Pattern matching is not a text-accuracy percentage.
Review words, punctuation, signatures, and map details before accepting results.
The repeated-diagonal mode does not promise complete watermark removal or infer
obscured characters; its protected area depends on detected horizontal content.

`tests/test_scan_patterns.py` checks the supported gray layouts, rejection of mismatched pages,
pixel preservation, partial diagonal cleanup, cancellation, packaging assets,
and application preview/batch/selective export. Customer-file acceptance checks
are run locally and are not committed to the repository.

Run `python -m unittest tests.test_app`, `python -m unittest tests.test_overlays`, `python -m unittest tests.test_quality`, and `python -m unittest tests.test_scans` in the Windows source environment. Tests exercise cleanup boundaries, text preservation, rotated/cropped PDF export, page retention, local request protection, upload validation, preview, batch processing, selective export, and session cleanup. Word-repair tests also verify selection validation, exact-case donor matching, preview isolation, confirmation, stale-preview rejection, outside-rectangle preservation, reprocessing, exported pixels and exact undo. Quality tests cover negative scans, manual overrides, normal-page preservation, reference metrics and benchmark validation.


### Small repeated gray copy marks
Choose **Small repeated copy** (also suggested automatically for supported scans).
The offline method recognizes many matching glyphs, adjusts their scale, reverses
the supported gray overlay inside matched areas, and preserves native pixels outside
those areas. It supports ordinary RGB scans and scans nested in tiling PDF patterns.
Preview and export use the same lossless native-image result; no AI or OCR rewriting
is used. Review each cleaned page before selecting it for export: overlapped ink,
dark headers, and black margins can retain marks. Original security printing is not
a cleanup target. This is not a guarantee of exact reconstruction or total removal.

## Kane County dotted outline

Choose **Kane County · dotted outline** for the supported 1728 x 2160 bilevel
scan with a diagonal outlined “Unofficial” stamp. Upload suggestions recognize
the exact watermark geometry and dot positions. Preview, clean all pages, and
review before downloading; matching cleaned pages start selected.

Cleanup changes only the 6,063 known watermark pixels. Every other native pixel
is preserved exactly, including punctuation, black text, and signatures. Tiny
intersections are estimated from neighboring ink with a local filter, so they
are not guaranteed to match an unavailable clean original. No OCR, cloud, or AI
is required. Blank, shifted, differently sized, and unmatched pages stay original.
The watermark-only profile is bundled in Windows packages; customer documents
are not bundled. Regression coverage is in `tests/test_kane_outline.py`.

## Separate gray Un-Official overlays

Choose **Text & website marks** for the supported gray diagonal “Un-Official”
PDF text overlay on search packages and large maps. Upload suggestions detect
this variant automatically. Preview, whole-document cleanup, and export use
the same PDF-object removal in `clearpage/pdf/pdf_overlays.py`.

The detector checks the terminal text block, exact character mapping, font,
color, opacity, rotation, and underlying scan. It removes only the matching
overlay commands; original scan bytes, page geometry, and unselected pages
remain unchanged. This variant does not reconstruct letters or use AI.
Flattened or differently encoded marks require a different supported method.
Synthetic positive and negative cases are in `tests/test_unofficial_overlay.py`.

## Diagonal licensing text on tiled scans

Choose **Diagonal licensing text** for the supported line beginning “LICENSED
TO FIRST AMERICAN DATA TREE LLC”. Upload suggestions use the same detector as
preview and processing. This variant supports the verified 2560-pixel-wide,
3312- or 4208-pixel-tall bilevel scans stored as five or six grayscale bands.
It checks the PDF placement commands and the watermark's native pixel position;
unknown layouts, shifted marks, grayscale scans, and unmatched pages stay original.

Preview, processing, and export share `clearpage/presets/licensed_diagonal.py`. Only the 39,629
profile pixels are interpolated; every other native pixel, page dimension, and
placement command is unchanged. No OCR or AI rewrites words. Intersections with
text and signatures are estimates, so review and select cleaned pages before
exporting. Unselected pages stay original. The packaged profile contains only
watermark geometry and image-placement instructions, not customer document text.
Regression coverage is in `tests/test_licensed_diagonal.py`.

## Hocking County outline

Choose **Hocking County outline** when a scanned page has the diagonal outlined
line “HOCKING COUNTY RECORDER THIS IS AN UNOFFICIAL COPY.” ClearPage suggests
it after sampling the upload, then checks every page separately during cleanup.
Unmatched pages and pages excluded at export keep their original scans.

The preset works locally on supported grayscale scans stored as RGB images;
it does not require AI or OCR. It compares the shape and tone of the outline
before removing it. Review intersections with words, signatures and seals: an
ambiguous fragment may remain to preserve the underlying ink.
