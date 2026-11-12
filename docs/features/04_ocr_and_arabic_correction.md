# OCR and Arabic text repair

## What it does

Makes Arabic PDFs searchable. A scanned or badly produced Arabic PDF often carries a text layer
that *looks* fine and cannot be searched; some pages carry no text at all. Two passes fix this
during ingestion, and both keep the citation highlight working.

## How it works

PyMuPDF loses no glyphs, but a text layer is a list of drawing instructions, not a document, and
it breaks three ways at once:

| Fault | Extracted | Should be |
| --- | --- | --- |
| a space inside a word | `لبنا ن` | `لبنان` |
| a space lost between words | `منقضا ء` | `من قضاء` |
| two letters transposed | `امل تن` | `المتن` |

The third comes from the ligature decomposition emitting a joined pair in visual, not logical,
order, so `لم` arrives as `مل`.

### The correction pass (text that is there but damaged)

`src/application/services/ingest/TextCorrectionService.py`, run by the postprocess stage. Each
page is handed to a model told to reproduce it and repair only extraction damage; the prompts are
in `src/application/ocr_prompts/`, where the rules are mostly prohibitions because a small model
asked to "clean up" a page will translate it, summarise it, or answer a question it contains.
Three guards make a rewrite safe to store: the response is validated against a schema whose page
list is *required*, corrections are mapped back through a **batch-local** number, not a page
index, and anything outside ±`POSTPROCESS_LENGTH_TOLERANCE` of the original length is rejected as
a summary and the page keeps its extracted text.

Measured on one page of a 222-page book, ten phrases whose correct form is not in doubt:

| | correct | time |
| --- | --- | --- |
| raw extraction | 1/10 | — |
| abstract rules, no examples | 9/10 | 79.1s |
| worked examples in the prompt | 9/10 | 12.0s |

Both figures are with the reasoning scratchpad **off**: left on, the model spends its whole token
budget thinking and returns no JSON at all. Repair is real but partial, strongest at the top of a
page.

### OCR of unreadable pages (no usable text at all)

`ProcessService.ocr_unreadable_pages`, called by the parse stage. `qalam` (Rust, reads the PDF's
own text layer) says which pages of a batch need OCR: a scan, a title page drawn as a picture,
glyphs that decode to nothing. Those are read with `tesseract-best` (the `tessdata_best` model,
Arabic and English together). Output that does not read as text is dropped, so a cover
photograph stays empty instead of indexing as noise. Uploaded pictures go through the same engine
(`ocr_image_bytes`). A missing engine is a warning, never a failed upload.

How many pages are OCR'd at once is the smallest of the pages there are, the CPUs this task's
share of the worker allows, and half the free memory at 256 MB a page.

### The highlight survives

A replaced page's text no longer matches the string its word boxes were measured against. The
length ratio is recorded per page, chunk offsets are scaled through it, and the citation is
marked approximate (`"approx": 1`) instead of silently pointing at the wrong sentence.

### The benchmark

`src/application/arabic_extraction/` holds the extractors and a benchmark harness that compared
ten extraction paths on **accuracy** (synthetic pages with known ground truth: `tesseract-best`
0.172 word error rate against 0.545 for the distro `ara` pack) and **cost** (2.29 s/page on two
cores). Qari-OCR is far more accurate at 0.063 but wants 5 GB of VRAM and 33.9 s a page, so it
runs as an optional remote extractor. See that package's `README.md` and
`benchmark/reports/FINDINGS.md`.

## Settings

| Setting | Does what |
| --- | --- |
| `POSTPROCESS_ENABLED` / `POSTPROCESS_MODEL_ID` | the correction pass and its model, qualified as `<source>/<tag>` |
| `POSTPROCESS_PAGES_PER_CALL` | pages per *model call*, inside a batch |
| `POSTPROCESS_CONCURRENCY` | model calls in flight per batch |
| `POSTPROCESS_NUM_CTX` / `POSTPROCESS_MAX_TOKENS` | context window and output cap for a page-sized prompt |
| `POSTPROCESS_LENGTH_TOLERANCE` | how far a correction may differ in length before it is rejected |
| `OCR_UNREADABLE_PAGES` | OCR pages with no usable text layer |
| `OCR_WORKERS` | pages OCR'd at once. `0` = sized from CPUs and memory |
| `TESSDATA_BEST` | where the `tessdata_best` models live |
| `QALAM_WORD_GAP` | qalam's word-break threshold; needs the patched build |

## Tests

`tests/ingestion/ocr/` (extractors, language profile, qalam, unreadable pages, pool sizing),
`tests/providers/test_text_correction.py`.

## Known limits

- The correction model tested on Arabic changes little; it helps English more.
- `qalam` 0.1.1 can return far too much text for a page: [guard](../roadmap/qalam_overreturn_guard.md).
  The application only asks it *which* pages need OCR.
- The per-page OCR re-read of badly spaced Arabic was removed because batched ingest never
  reached it: [roadmap](../roadmap/arabic_reread_in_batched_ingest.md).
- OCR calls are one page each: [batching](../roadmap/ocr_batch_pages_per_call.md).
