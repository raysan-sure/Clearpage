# GitHub comparison for ClearPage 1.6

Official GitHub metadata and pinned source inspected on 2026-10-26. Star counts measure popularity, not document restoration accuracy.

| Project | Stars at inspection | Findings and use |
| --- | ---: | --- |
| [D-Ogi/WatermarkRemover-AI](https://github.com/D-Ogi/WatermarkRemover-AI) | 2,065 | MIT. Florence-2 locates regions; LaMa reconstructs pixels. Inspected box/stroke masks, verification, padding and inference. ClearPage now uses the same verified LaMa model with its existing precise masks. Florence is not included: a broad region over a deed would discard too much surviving text. |
| [Sanster/IOPaint](https://github.com/Sanster/IOPaint) | 23,297 | Apache-2.0; archived August 2025. Uses the same big-lama.pt artifact as D-Ogi. ClearPage follows its normalization/padding conventions, with bounded tiles, exact mask compositing and local CPU inference. No IOPaint server is needed. |
| [advimman/lama](https://github.com/advimman/lama) | 10,346 | Apache-2.0; original WACV 2022 inpainting network. This is the actual pretrained neural model now included in the Windows package. No training or fine-tuning was performed. |
| [Purestone/purescan](https://github.com/Purestone/purescan) | Not refreshed | Existing MIT maximum-projection/multiply-inversion integration remains available for supported blended marks. It is a separate method, not a neural network. |
| [guillaumemeyer/watermarks-remover](https://github.com/guillaumemeyer/watermarks-remover) | Not refreshed | Previously inspected metadata/provenance cleanup and regeneration paths do not solve exact restoration of the visible watermark embedded in the scanned deed. |
| [ZeroxZhang/pdf-watermark-remover](https://github.com/ZeroxZhang/pdf-watermark-remover) | Not refreshed | Structural PDF watermark deletion cannot separate a mark already burned into scanned image pixels. |

## Additional research and implementation in 1.6

| Project | Stars observed (rounded where GitHub rounds) | Decision |
| --- | ---: | --- |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 91.7k | Apache-2.0. Added the pretrained PP-OCRv6 small recognition model to read original and cleaned word crops locally. |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | 8.3k | Apache-2.0. Inspected version 3.10.0; adapted recognition normalization and CTC decoding into a bounded CPU-only adapter. Its packaged model and license notice supply an exact artifact hash. |
| [Royalvice/DocDiff](https://github.com/Royalvice/DocDiff) | 363 | MIT. Downloaded released seal-removal weights and ran its coarse model and 100-step diffusion on difficult crops. It retained this black watermark or left already damaged letters. Not included. |
| [ZZZHANG-jx/DocRes](https://github.com/ZZZHANG-jx/DocRes) | 682 | MIT. Inspected documented dewarping, shadow, binarization and deblur tasks. No demonstrated solution to black-on-black occlusion here. Not runtime-tested or included. |
| [bcmi/SLBR-Visible-Watermark-Removal](https://github.com/bcmi/SLBR-Visible-Watermark-Removal) | 258 | Natural-image CLWD watermark separation; no clear repository license found in inspection. Not runtime-tested or redistributed. |
| [MRUIL/WDNet](https://github.com/MRUIL/WDNet) | 113 | Natural-image watermark decomposition; no clear repository license found in inspection. Not runtime-tested or redistributed. |
| [Jurph/untext](https://github.com/Jurph/untext) | 4 | MIT. Useful template/consensus mask ideas, but its LaMa/Telea reconstruction would repeat existing methods. Not integrated. |

Popularity guided the shortlist, not acceptance. DocDiff was actually evaluated and rejected for this scan; the other unintegrated systems above are not claimed as tested improvements. No model was fine-tuned. Company data stays local.

The new implemented path is OCR cross-checking plus font fitting to authentic surviving source pixels. It addresses specific damaged words after cleanup rather than adding another unconstrained image generator. Preliminary trials repaired selected words such as “follows:” and “subsequent” more cleanly. They also produced incorrect high-confidence readings, which is why changed spellings are checked against intact document words and uncertain suggestions require review. Short words are not automatically font-fitted because they do not reliably distinguish font families.

The Windows release's VERIFICATION.txt records the final integration test results. No clean reference copy of the 73-page document is available. Neither this update nor the older benchmark establishes 88–95% word accuracy or trace-free output.

## Earlier LaMa measurements


The verified LaMa model ran locally on 512 x 512 difficult crops from pages 1, 13, 21 and 40, comparing the original, existing cleanup and LaMa outputs. It removed more visible watermark material, but some reconstructed letters remained distorted. There is no clean ground-truth copy of the document, so its text accuracy cannot be assigned a percentage.

On one synthetic 512 x 512 text fixture with a known clean original and an opaque diagonal watermark, using the same mask:

| Measure | OpenCV Telea | LaMa |
| --- | ---: | ---: |
| Mean absolute RGB error inside mask (0-255; lower is better) | 29.86 | 17.13 |
| Covered original dark pixels still dark after repair | 11.5% | 66.1% |
| Pixels outside mask preserved exactly | Yes | Yes |

The second metric is **pixel recall, not word or character accuracy**; it can also count incorrectly placed ink. This single fixture does not establish general performance. Visual inspection still showed imperfect lettering. Both original-based and cleaned-input inference were tested on the real crops; neither reliably recovered all hidden letters.

## Implementation decision

LaMa is an optional cleanup mode and targeted word-preview method. Whole-page AI results are unchecked for export until selected. It runs at the existing PDF preview resolution without shrinking entire pages, in 384-pixel output tiles with 64-pixel context. Only selected pixels are composited; model outputs outside the mask are discarded. Predictions are cached before word repairs. Exact scanned-word matches and explicitly confirmed repairs are applied afterward and retained during reprocessing.

This adds a real local pretrained model. It does not establish 100% recovery, trace-free output, or factual verification of names/numbers. Confirmed text and clean scanned occurrences remain available when AI cannot recover the lettering.

Pinned commits, weight URL, exact hash and attribution are in `vendor/AI-PROVENANCE.txt`. The source ZIP omits the 206 MB weights; `download_model.py` verifies and installs that fixed artifact. The Windows AI package includes the runtime and weights and needs no internet connection for processing.
