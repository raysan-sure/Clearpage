# Private quality benchmark

Start with 20–30 representative PDFs and **verified clean reference copies**.
Suggested categories: separate website overlay, faint scan watermark, dark
overlapping watermark, negative scan, and normal page (a control that should
remain unchanged). Keep this collection locally in `benchmark-local/`, which
Git ignores. Do not commit customer PDFs or reports containing their details.

Use the same original scans, crop positions, rotation, page order and alignment
in each source/reference pair. A newly scanned, shifted, resized or retyped
reference is unsuitable for exact pixel comparisons. The software checks page
geometry, but cannot verify that the reference depicts the right page or that
its wording is correct. Inspect the alignment yourself.

## Check one preview in the app

After **Clean all pages**, open **Quality check**, then **Choose trusted clean
PDF**. It compares the current **After preview** with that page in the reference
PDF, regardless of the export checkbox. The reference must have the same number
of pages. Save the JSON report if useful. The uploaded reference is deleted
after comparison and is not added to the reviewed-repair library.

## Compare releases across the whole collection

Save each release's exported results in a separate local folder. Create
`benchmark-local/cases.json` using paths relative to that manifest:

```json
{
  "cases": [
    {
      "id": "negative-01",
      "category": "negative scan",
      "source": "source/negative-01.pdf",
      "result": "v1.8/negative-01.pdf",
      "reference": "reference/negative-01.pdf"
    }
  ]
}
```

Add one entry per document. Optional `"pages": [1, 3]` selects 1-based page
numbers; omitting it measures every page. From a configured source environment:

```powershell
.\.venv\Scripts\python.exe benchmark.py benchmark-local/cases.json --output benchmark-local/v1.8-report.json
```

Repeat with the same cases and references after each update, changing only the
result paths. Reports include every measured page and means by category. Each
category mean weights measured pages equally and states how many values exist.

## Interpret the measurements

- **Correction error before/after:** mean absolute RGB error (0–255, lower is
  better) in pixels where source and reference differ by more than 8 in at least
  one channel. Improvement indicates closer visual agreement in those areas.
- **Matching original pixels preserved:** percentage of source pixels exactly equal to the reference
  that the result left exactly unchanged. This checks collateral edits.
- **Dark pixel recall:** percentage of reference dark pixels also dark in the
  result. Erasing text reduces this score.
- **Dark pixel precision:** percentage of output dark pixels also dark in the
  reference. Extra dark remnants reduce this score. Dark means mean RGB <128.
- **Mean RGB error:** whole-page error; large blank areas can dominate this
  measurement, so do not use it alone.

`null` means not applicable, such as a page with no dark pixels or no correction
region. These metrics are **not word or character accuracy**, and dark-pixel
matches do not establish correct spelling. Manually check critical names,
numbers, signatures and text under the watermark. Negative scans change almost
the entire page, so compare their category separately from watermark removal.


## Release gates (2.2)

Run `python scripts/benchmark_suite.py --output-dir build/benchmark` before
packaging. It builds an independent synthetic reference corpus (11 pages across
5 categories) and fails if native-object results differ from the known reference,
pristine pixels change, or faint reference ink is damaged. The overlap scan may
retain watermark residue; the report records it rather than calling it success.
The build artifact includes this report. Synthetic samples are not a substitute
for representative company scans and independently verified clean references.

New measures: **corrected watermark pixels** counts source/reference differences
over 8 that end within 8 of the reference; **faint ink match** measures reference
pixels with mean RGB 128�224 that end within 8 in every channel. Unexpected changes
count edits to pixels that matched the reference exactly before cleanup.

Compare the same private cases across releases:

```powershell
python benchmark.py benchmark-local/cases.json --output benchmark-local/new.json --baseline benchmark-local/previous.json --tolerance 0.1
```

A missing baseline page or a worsened tracked metric beyond the tolerance returns
exit code 1 after writing the report. Tolerance is in percentage points for percent
metrics and RGB levels for errors. Do not mix references, resolutions, crops or
rendering environments between runs. This checks pixels, not spelling or meaning.
