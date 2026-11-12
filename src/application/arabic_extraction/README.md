# `arabic_extraction` — Arabic text extraction, and choosing how to do it

This package exists because the Arabic PDFs in this project are **not an OCR
problem in the usual sense**. They have text layers. The text is simply wrong,
in ways a character count does not reveal.

One line from `ذخائر_لبنان.pdf`, as each extraction path returns it. The correct
reading is *"اليسار حينئذٍ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان نريار الثالث"*:

| path | output | what went wrong |
| --- | --- | --- |
| `pymupdf-raw` | `اليسارحينئذ … وحدثفي أيامبيكماليون` | words **fused** — the producer emitted no space glyphs |
| `pymupdf-words` | `ا ليسا ر … و حد ث في أ يا م` | words **shattered** — split at every non-joining letter |
| `pdfplumber` | `ثلاثلا رايرن نامار نأ` | the line is **reversed** |
| `tesseract-best` | `اليسار حينئذٍ بديدو … أن رامان نيرار الثالث` | one word wrong in the line |

Every one of those is real Arabic in real codepoints. Only the last can be
searched. **Re-reading the rendered page with OCR beats the PDF's own text
layer on this corpus** — which is not the result anyone expects, and is why the
comparison is worth running rather than reasoning about.

## The two questions

`language.py` answers both cheaply, by counting codepoints. No model, no
dependency, no threshold to tune.

**Is this Arabic?** Script is decidable from the codepoint. A statistical
detector would be answering a harder question (Arabic vs Persian vs Urdu) worse,
on exactly the short mangled strings a broken text layer produces.

**Can this text be searched?** The question that actually matters, and one no
language detector answers. Two failure modes, both invisible to a character
count and both visible in the *rate* of spacing:

- **presentation forms** — `U+FB50–FDFF`, `U+FE70–FEFF`: the glyph variants a
  font draws rather than the letters anyone types. They render identically and
  compare as different characters. NFKC folds them, and the ingestion pipeline
  already does this.
- **mis-segmentation** — fragmented or fused, as above. Nothing short of
  re-reading the page fixes it.

The healthy band (0.13–0.22 spaces per character) is measured, not guessed; the
numbers that set it are in the module.

## In the application

Two things in the ingestion path use this package, both in the parse stage
(`application/tasks/jobs/ingest/parse.py`):

- **Pages with no usable text layer** (`OCR_UNREADABLE_PAGES`, on by default).
  `ProcessService.ocr_unreadable_pages` asks `qalam` which pages of a batch
  need OCR -- a scan, a title page drawn as a picture, glyphs that decode to
  nothing -- and reads those with `tesseract-best` (Arabic and English
  together). Output that does not read as text is dropped, so a cover
  photograph stays empty rather than indexing as noise.
- **Uploaded pictures.** `ProcessService.ocr_image_bytes` reads the text off
  a PNG/JPEG/WebP source with the same engine.

| setting | default | what it decides |
| --- | --- | --- |
| `OCR_UNREADABLE_PAGES` | `True` | OCR the pages qalam flags; off leaves them empty |
| `TESSDATA_BEST` | `/usr/share/tessdata-best` | where `ara.traineddata` from `tessdata_best` lives |
| `OCR_WORKERS` | `0` | pages read at once; `0` sizes it from the CPUs and memory available |
| `QALAM_WORD_GAP` | `0.15` | qalam's word-break threshold (patched build only) |

**A missing engine is a warning, not a failure.** If `qalam` or
`tesseract-best` cannot run -- no `tesseract` binary, no `ara.traineddata`
under `TESSDATA_BEST` -- the reason is logged and the pages keep what the PDF
gave. Failing an upload over a missing OCR binary is worse than indexing
imperfect text.

**Badly spaced Arabic is not re-read with OCR any more.** Pages whose text is
there but fragmented or run together are repaired by the model correction pass
(`TextCorrectionService`), which keeps the word boxes a citation highlight is
drawn from. The per-page OCR re-read that used to do this
(`OCR_ENABLED` / `OCR_EXTRACTOR` / `OCR_MIN_CHARS`) was removed because batched
ingest never reached it; `docs/roadmap/arabic_reread_in_batched_ingest.md`
records what it did. `profile()` in `language.py`, which made its per-page
decision, is still used by the benchmark.

**`qalam` 0.1.1 has a known bug.** On some documents it returns 200-300x too
much text for a page (a repeating header/footer), paired with a false
`confidence: 1.0` -- see `benchmark/reports/qalam/report/report.md`. The
application only asks qalam *which pages need OCR*, not for their text, so the
bug does not reach stored chunks.

Budget **2.3-2.7 s per OCR'd page** on CPU (2.29 s pinned to two cores). See
`benchmark/reports/FINDINGS.md`.

## The extractors

`ArabicExtractor` is the contract: `_extract(page) -> str`, plus an
`available()` that returns **a reason** when it cannot run. That reason is the
point — a benchmark table that omits an engine is read as "it scored nothing"
rather than "it was never installed".

| name | kind | notes |
| --- | --- | --- |
| `pymupdf-raw` | text layer | free, and the baseline OCR must beat |
| `pymupdf-words` | text layer | what `PdfLayoutController` does, for citation boxes |
| `pdfplumber` | text layer | third opinion; reverses RTL |
| `qalam` | text layer | Rust, no OCR, logical reading order. In production it picks the pages that need OCR — see the known-bug caveat above |
| `tesseract` | classical | CPU, ~1.4 MB model, the distro `ara` pack |
| `tesseract-best` | classical | CPU, 12.6 MB `tessdata_best` — a different model, not a speed setting. **The production OCR engine** |
| `easyocr` | classical | PyTorch, ~500 MB |
| `paddleocr` | classical | dedicated Arabic recognition model |
| `surya` | classical | transformer, GPU-oriented |
| `qari` | VLM | Qwen-VL fine-tuned on Arabic, local, fits 8 GB |
| `gemini` | VLM | hosted, metered, the accuracy yardstick |

Every engine import is **lazy**, inside the method that needs it. That is what
lets one package hold both the benchmark and the production path: the image
carries `qalam` (a base dependency: it picks the pages that need OCR),
`tesseract`, its `ara` model and `pytesseract` (a wrapper around the binary),
and importing this package never pulls in torch, paddle or transformers
unless an engine that needs them is actually built. The heavy
engines still live in a separate venv.

## Measuring

`benchmark/metrics.py` reports two kinds of number, because neither is trustworthy alone.

**Exact.** `benchmark/fixtures.py` renders pages from strings it holds, so CER and WER are
computed against known truth. This is the only defence against a vision model
producing fluent Arabic that was never on the page — every intrinsic signal
scores a confident hallucination perfectly.

**Intrinsic.** Real pages have no reference, so what is measured is whether the
output has the *shape* of searchable Arabic, plus cross-engine agreement.

CER and WER are both reported because on Arabic they disagree, and the
disagreement is the finding: fragmenting `اليسار` into `ا ليسا ر` changes no
characters and destroys every word. **WER is the number that predicts whether
retrieval works.**

## The modules

| module | does |
| --- | --- |
| `language.py` | script and searchability, by counting codepoints |
| `base.py` | the `ArabicExtractor` contract, `Page`, timing and telemetry |
| `registry.py` | what can run here, and **why not** when it cannot |
| `extractors/` | the engines, one file per family |
    ├── traditional.py     CPU engines: tesseract, easyocr, paddleocr, surya
    ├── vision_models.py   a local GPU model: qari
    ├── hosted.py          someone else's GPU over an API: gemini, openrouter
    ├── remote.py          your own GPU over HTTP: qari-remote
    └── text_layer.py      what the PDF already claims to contain
| `benchmark/fixtures.py` | pages rendered from known text, so CER/WER are exact |
| `benchmark/metrics.py` | CER, WER, agreement, and the intrinsic signals |
| `benchmark/runner.py` | runs the suites |
| `benchmark/merge.py` | folds the per-engine runs back into one report |
| `benchmark/report.py` | plots, tables and snapshots |

Results land in `benchmark/results/`, reports in `benchmark/reports/`. Page images and raw
transcripts are gitignored — they are renderings of copyrighted books, and a
benchmark is no reason to commit someone else's book.

## One engine per process

`benchmark/__main__.py` benchmarks whatever `--only` names, and the driver runs each
engine in its own process. That is not fastidiousness:

- a combined run **deadlocked**, eighty threads asleep on a futex, after
  several PyTorch-based engines were loaded into one interpreter;
- paddle, surya and qari want mutually incompatible pins;
- `peak_rss_mb` is a process high-water mark, which is an engine's footprint
  only when the engine had the process to itself. Subtracting a before-reading
  from an after-reading measures nothing, because CPython does not return freed
  pages to the OS — whichever engine loads first is charged for memory every
  later engine then reuses for free.

`benchmark/merge.py` puts the pieces back together, preferring the most recent run of
each engine.

## Running it

```bash
python -m arabic_extraction.benchmark --list                     # what can run here, and why not
python -m arabic_extraction                            # synthetic, exact CER/WER
python -m arabic_extraction.benchmark --corpus ~/pdfs            # add real documents
python -m arabic_extraction.benchmark --only tesseract-best qari # narrow it
```

Generate a detailed comparison report with plots:

```bash
python -m arabic_extraction.benchmark --only tesseract --out ocr-benchmark
python -m arabic_extraction.benchmark.report ocr-benchmark/synthetic.json --output ocr-report
```

The report includes CER/WER, median page time, CPU time, peak resident memory,
optional peak GPU allocation, usability, and an availability/requirements
matrix. Plots are written as both PNG and SVG. GPU values are shown only when
the engine exposes CUDA allocator telemetry; a missing value is not a zero.

The heavy engines need their own environment:

```bash
uv venv ocrbench && uv pip install --python ocrbench/bin/python \
    pymupdf pillow pytesseract pdfplumber torch easyocr transformers surya-ocr
sudo dnf install tesseract tesseract-langpack-ara
export TESSDATA_BEST=/path/to/tessdata_best   # for the tesseract-best row
```

`ara.traineddata` for `tesseract-best` comes from
[tessdata_best](https://github.com/tesseract-ocr/tessdata_best).

`tesseract-best` alone needs none of that in the app: the Dockerfile installs
`tesseract-ocr` and `tesseract-ocr-ara`, downloads `ara.traineddata` and
`eng.traineddata` from `tessdata_best` into `/usr/share/tessdata-best` at build
time (so the container starts with no network), and `pytesseract` is in
`src/pyproject.toml`. Running it outside the image needs the same two pieces:
the engine and the `ara` pack from your distribution, plus that directory.

One wrinkle worth knowing: `TesseractBestExtractor` reads `TESSDATA_BEST` from
`os.environ`, while `Settings` reads it as a field. Under compose they are the
same value, because `env_file:` puts it in the process environment. A value
that only exists in `src/.env` reaches `Settings` and *not* the extractor,
which then reports `TESSDATA_BEST is not set` — the warning path above, so
uploads keep working and the pages simply are not re-read.
