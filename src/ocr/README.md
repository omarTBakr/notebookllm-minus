# `ocr` — Arabic text extraction, and choosing how to do it

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

The benchmark picked `tesseract-best`, and it is now wired in.
`ProcessController._reread_unusable_arabic` runs on the PDF layout path
(`PDF_LOADER=pymupdf`), after `extract_pages` and before the Documents are
built, and replaces the text of the pages it re-reads. Four settings in
`src/utils/config.py`:

| setting | default | what it decides |
| --- | --- | --- |
| `OCR_ENABLED` | `False` | nothing happens at all unless this is set |
| `OCR_EXTRACTOR` | `tesseract-best` | which engine the registry is asked to build |
| `TESSDATA_BEST` | `/usr/share/tessdata-best` | where `ara.traineddata` from `tessdata_best` lives |
| `OCR_MIN_CHARS` | `80` | how much text a page needs before its spacing is judged |

**Off by default, and per page.** `profile()` decides for each page, and all
three answers must agree: the page is Arabic, its text layer is unusable
(fragmented or run-together), and it has at least `OCR_MIN_CHARS` characters —
below which a plate or a chapter heading would be re-read on a ratio computed
over nothing. Everything else keeps the text layer, which is not a compromise:
a healthy text layer is the characters the author typed, and OCR trades those
for a guess at the pixels.

**A re-read page keeps an approximate citation highlight.** Highlights come
from `highlight_metadata`, which maps character offsets onto the word boxes
*that page's text was built from*. Replacing the text makes those offsets
address a different string — but OCR returns no coordinates at all, so those
boxes stay the only positional information in existence, and both strings read
the same page in the same order. The page is kept, the length ratio between the
two renderings is recorded, `split_file` scales offsets through it, and the
result is marked `"approx": 1` so a reader can tell a close highlight from an
exact one. Measured on a 222-page book: 378 of 378 chunks highlighted.

**A missing engine is a warning, not a failure.** If `OCR_ENABLED` is set but
the extractor cannot run — no `tesseract` binary, `TESSDATA_BEST` unset, no
`ara.traineddata` under it — the registry's `available()` reason is logged and
the text layer is kept. Failing an upload over a missing OCR binary is worse
than indexing imperfect text. A per-page OCR failure behaves the same way: that
page keeps its original text, and its highlight with it.

Budget **2.3–2.7 s per re-read page** on CPU (2.29 s pinned to two cores), and
only for pages that fail the profile. See `reports/FINDINGS.md`.

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
| `tesseract` | classical | CPU, ~1.4 MB model, the distro `ara` pack |
| `tesseract-best` | classical | CPU, 12.6 MB `tessdata_best` — a different model, not a speed setting. **The production engine** |
| `easyocr` | classical | PyTorch, ~500 MB |
| `paddleocr` | classical | dedicated Arabic recognition model |
| `surya` | classical | transformer, GPU-oriented |
| `qari` | VLM | Qwen-VL fine-tuned on Arabic, local, fits 8 GB |
| `gemini` | VLM | hosted, metered, the accuracy yardstick |

Every engine import is **lazy**, inside the method that needs it. That is what
lets one package hold both the benchmark and the production path: the image
carries only `tesseract`, its `ara` model and `pytesseract` (a wrapper around
the binary), and importing this package never pulls in torch, paddle or
transformers unless an engine that needs them is actually built. The heavy
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
| `benchmark/fixtures.py` | pages rendered from known text, so CER/WER are exact |
| `benchmark/metrics.py` | CER, WER, agreement, and the intrinsic signals |
| `benchmark/runner.py` | runs the suites |
| `benchmark/merge.py` | folds the per-engine runs back into one report |
| `benchmark/report.py` | plots, tables and snapshots |
| `pipeline.py` | the same decision — text layer, or OCR — as a standalone object for callers outside the app |

The application does not go through `pipeline.py`: it needs a decision *per
page* and it has to drop the re-read pages from `_pdf_pages`, so
`ProcessController._reread_unusable_arabic` applies the same rule directly over
`profile()` and `registry.build()`. `ArabicOcrPipeline` remains the one-page
form of it for anything calling this package on its own.

Results land in `results/`, reports in `reports/`. Page images and raw
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
python -m ocr.benchmark --list                     # what can run here, and why not
python -m ocr                            # synthetic, exact CER/WER
python -m ocr.benchmark --corpus ~/pdfs            # add real documents
python -m ocr.benchmark --only tesseract-best qari # narrow it
```

Generate a detailed comparison report with plots:

```bash
python -m ocr.benchmark --only tesseract --out ocr-benchmark
python -m ocr.benchmark.report ocr-benchmark/synthetic.json --output ocr-report
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
