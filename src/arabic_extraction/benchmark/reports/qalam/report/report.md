# qalam vs. the existing extractors

`qalam` 0.1.1 ([misraj-ai/qalam](https://github.com/misraj-ai/qalam), Rust with
published Python bindings, `pip install qalam`) was added as
`arabic_extraction/extractors/qalam_extractor.py` and run against the two real
PDFs in `fineTuning/assets/` — the same corpus `arabic_extraction/README.md`'s ground-truth
line comes from. Its API, confirmed against the installed package rather than
its README (`python -c "import qalam; help(qalam)"`):

- `qalam.extract_text(path)` — the whole document's text in one call.
- `qalam.Document(path)` — parses the whole PDF eagerly on construction, then
  exposes `.text` (whole document), `.confidence` (mean per-page, 0–1),
  `.pages_needing_ocr` (1-based page numbers it declines to hand back text
  for), and `.page(n)` (1-based) for per-page access — `.text`, `.confidence`,
  `.verdict` (`"ok"` / `"degraded"` / `"needs_ocr"`), `.needs_ocr`, `.reasons`.

`QalamExtractor` wraps `.page(n).text` and caches the parsed `Document` by
path, since parsing is eager and per-document while the harness asks for one
page at a time. `QalamExtractor.inspect(path)` surfaces `.confidence` /
`.pages_needing_ocr` outside the benchmark loop, since the `ArabicExtractor`
contract only returns text.

## `ذخائر_لبنان.pdf` — the automated comparison

Everything below this line, through "Interpretation", is
`python -m arabic_extraction.benchmark`'s own output (`--corpus`, `--real-pages 5`) — the
same tables and sampling this package already produces for every other
engine, run here against `pymupdf-raw`, `pymupdf-words`, `qalam`, `tesseract`
and `tesseract-best` (the extractors that survive `registry.survey()` in this
environment; see "What could not run here" below).

## Comparison

| extractor | pages | success | CER | WER | seconds/page | CPU seconds | peak RSS MiB | peak GPU MiB | usable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pymupdf-raw | 5 | 4/5 | - | - | 0.0 | 0.0 | 154.91 | - | 0/4 |
| pymupdf-words | 5 | 4/5 | - | - | 0.0 | 0.0 | 155.1 | - | 0/4 |
| qalam | 5 | 4/5 | - | - | 0.0 | 0.0 | 155.1 | - | 4/4 |
| tesseract | 5 | 4/5 | - | - | 0.94 | 0.17 | 172.91 | - | 4/4 |
| tesseract-best | 5 | 4/5 | - | - | 1.81 | 0.09 | 172.91 | - | 4/4 |

## Plots

- [Time](plots/time.png)
- [CPU time](plots/cpu.png)
- [Resident memory](plots/rss.png)

## Requirements and allocation

| extractor | available | text layer | GPU | requirements | description |
| --- | --- | --- | --- | --- | --- |
| pymupdf-raw | yes | yes | no | CPU | pymupdf get_text() — the page's own text layer, untouched |
| pymupdf-words | yes | yes | no | CPU | rebuilt from per-word boxes — what PdfLayoutController does |
| pdfplumber | no | yes | no | pdfplumber is not installed | pdfplumber's own word segmentation — a third opinion |
| qalam | yes | yes | no | CPU | qalam — logical reading-order text-layer extraction, no OCR |
| tesseract | yes | no | no | CPU | Tesseract 5 with the ara traineddata (CPU) |
| tesseract-best | no | no | no | TESSDATA_BEST is not set | Tesseract 5 with the tessdata_best ara model (CPU) |
| easyocr | no | no | yes | easyocr is not installed | EasyOCR ar+en, PyTorch detector + recogniser |
| paddleocr | no | no | yes | paddleocr is not installed | PaddleOCR with the arabic recognition model |
| surya | no | no | yes | surya-ocr is not installed | Surya detection + recognition (transformer, GPU-oriented) |
| qari | no | no | yes | transformers/torch are not installed | Qari-OCR (Qwen-VL fine-tune, Arabic-specific, local) |
| qari-remote | no | no | no | QARI_REMOTE_URL is not set (start arabic_extraction/colab/qari_server.ipynb) | Qari-OCR on a remote GPU (Colab + ngrok), over HTTP |
| gemini | no | no | no | GOOGLE_API_KEY is not set in the environment or .env | Gemini multimodal via the API (metered) |
| openrouter | no | no | no | OPENROUTER_API_KEY is not set in the environment or .env | A vision model on OpenRouter (default: minimax-m3 free tier) |

## The same line, every engine

Metrics describe output; this shows it. Read these before trusting any number above them.

### `ذخائر_لبنان.pdf` page 75

```
pymupdf-raw      ﻋﲆ اﻟﺴﻮاﺣﻞ وﺗﻘﺮﻳﺮ اﻟﺠﺰﻳﺔﻋﻠﻴﻬﻢ دﺧﻞ أﻫﻞﺑريوتﰲ اﻟﺘﻘﺮﻳﺮ« )ﻣﺠﻠﺔ املﴩقﻋﺪد ٣
pymupdf-words    ﻋﲆ ا ﻟﺴﻮ ا ﺣﻞ و ﺗﻘﺮﻳﺮ ا ﻟﺠﺰﻳﺔﻋﻠﻴﻬﻢ د ﺧﻞ أ ﻫﻞﺑ ريوت ﰲ ا ﻟﺘﻘﺮﻳﺮ« )ﻣﺠﻠﺔ امل ﴩ ق ﻋﺪ د ٣
qalam            على السواحل وتقرير الجزية عليهم دخل أهل بيروت في التقرير« )مجلة المشرق عدد ٣
tesseract        على السواحل وتقرير الجزية عليهم دخل أهل بيروت في التقرير» (مجلة المشرق عدد ”
tesseract-best   على السواحل وتقرير الجزية عليهم دخل أهل بيروت في التقرير» (مجلة المشرق عدد *
```

## Snapshots

Each real page has a rendered image and one text file per engine.
- [snapshots/ذخائر_لبنان-p112-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p112-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p112-pymupdf-words.txt](snapshots/ذخائر_لبنان-p112-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p112-qalam.txt](snapshots/ذخائر_لبنان-p112-qalam.txt)
- [snapshots/ذخائر_لبنان-p112-tesseract-best.txt](snapshots/ذخائر_لبنان-p112-tesseract-best.txt)
- [snapshots/ذخائر_لبنان-p112-tesseract.txt](snapshots/ذخائر_لبنان-p112-tesseract.txt)
- [snapshots/ذخائر_لبنان-p112.png](snapshots/ذخائر_لبنان-p112.png)
- [snapshots/ذخائر_لبنان-p149-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p149-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p149-pymupdf-words.txt](snapshots/ذخائر_لبنان-p149-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p149-qalam.txt](snapshots/ذخائر_لبنان-p149-qalam.txt)
- [snapshots/ذخائر_لبنان-p149-tesseract-best.txt](snapshots/ذخائر_لبنان-p149-tesseract-best.txt)
- [snapshots/ذخائر_لبنان-p149-tesseract.txt](snapshots/ذخائر_لبنان-p149-tesseract.txt)
- [snapshots/ذخائر_لبنان-p149.png](snapshots/ذخائر_لبنان-p149.png)
- [snapshots/ذخائر_لبنان-p186-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p186-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p186-pymupdf-words.txt](snapshots/ذخائر_لبنان-p186-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p186-qalam.txt](snapshots/ذخائر_لبنان-p186-qalam.txt)
- [snapshots/ذخائر_لبنان-p186-tesseract-best.txt](snapshots/ذخائر_لبنان-p186-tesseract-best.txt)
- [snapshots/ذخائر_لبنان-p186-tesseract.txt](snapshots/ذخائر_لبنان-p186-tesseract.txt)
- [snapshots/ذخائر_لبنان-p186.png](snapshots/ذخائر_لبنان-p186.png)
- [snapshots/ذخائر_لبنان-p38-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p38-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p38-pymupdf-words.txt](snapshots/ذخائر_لبنان-p38-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p38-qalam.txt](snapshots/ذخائر_لبنان-p38-qalam.txt)
- [snapshots/ذخائر_لبنان-p38-tesseract-best.txt](snapshots/ذخائر_لبنان-p38-tesseract-best.txt)
- [snapshots/ذخائر_لبنان-p38-tesseract.txt](snapshots/ذخائر_لبنان-p38-tesseract.txt)
- [snapshots/ذخائر_لبنان-p38.png](snapshots/ذخائر_لبنان-p38.png)
- [snapshots/ذخائر_لبنان-p75-pymupdf-raw.txt](snapshots/ذخائر_لبنان-p75-pymupdf-raw.txt)
- [snapshots/ذخائر_لبنان-p75-pymupdf-words.txt](snapshots/ذخائر_لبنان-p75-pymupdf-words.txt)
- [snapshots/ذخائر_لبنان-p75-qalam.txt](snapshots/ذخائر_لبنان-p75-qalam.txt)
- [snapshots/ذخائر_لبنان-p75-tesseract-best.txt](snapshots/ذخائر_لبنان-p75-tesseract-best.txt)
- [snapshots/ذخائر_لبنان-p75-tesseract.txt](snapshots/ذخائر_لبنان-p75-tesseract.txt)
- [snapshots/ذخائر_لبنان-p75.png](snapshots/ذخائر_لبنان-p75.png)

## Interpretation

CER measures character edits; WER measures retrieval-relevant word edits.
Lower is better for both. Time and CPU seconds are per page. RSS is the
process peak and GPU is the allocator peak when Torch is available; a dash
means the runtime could not expose that metric. Compare synthetic scores
only against other synthetic scores, and use real-document agreement and
manual review for pages without ground truth.

## Ground truth check — the `ذخائر_لبنان.pdf` line from `arabic_extraction/README.md`

`arabic_extraction/README.md` quotes one line from page 56 (1-based; `page.number == 55`)
and gives its correct reading as:

```
اليسار حينئذٍ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان نريار الثالث
```

Every extractor available here, on that exact page:

| extractor | reading | verdict |
| --- | --- | --- |
| `pymupdf-raw` | `اليسارحينئذ ٍبديدو ومعناه الهاربة. وحدثفي أيامبيكماليون أن راماننريار الثالث` | fused — no space glyphs, as the README describes |
| `pymupdf-words` | `ا ليسا ر حينئذ بديد و و معنا ه ا لها ر بة. و حد ث في أ يا م بيكماليو ن أن را ما ن ن ريار ا لثالث` | shattered — split inside almost every word |
| `qalam` | `اليسار حينئذٍ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان نيرار الثالث` | **one word wrong** (`نيرار` for `نريار`) — byte-for-byte identical to `tesseract-best`'s reading below |
| `tesseract-best` | `اليسار حينئذٍ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان نيرار الثالث` | one word wrong, exactly as `arabic_extraction/README.md` reports |

`qalam` reaches the production engine's exact accuracy on this line — reading
order correct, spacing correct, the same single transliteration slip — using
the PDF's own text layer rather than re-rendering and OCRing the page.
Page 75's line (in the auto-generated table above) goes the other way: `qalam`
reads the trailing Arabic-Indic numeral `٣` correctly where both `tesseract`
and `tesseract-best` substitute a stray symbol. Two lines is not a corpus, but
on both of them `qalam` is at least as accurate as the engine this package
already ships to production, at a small fraction of the cost (see "Cost" below).

## `دليلك-للدراسات-العليا-بالخارج-الإصدار-الثاني.pdf` — a bug, not a benchmark

This document (274 pages, 25 MB, image-heavy) is not comparable to the table
above, because `qalam` did not produce comparable output on it. Running the
automated harness here was tried and aborted: `benchmark.consensus()` computes
pairwise Levenshtein distance between every pair of extractions on a page, and
against `qalam`'s output on this document that pairwise comparison alone ran
for over ten CPU-minutes on a single page before being killed — the harness
was never designed for one engine returning three orders of magnitude more
text than the others.

**What `qalam` reports about itself:** parsing the whole document eagerly
(`qalam.Document(path)`) took 166–173 s and reported `confidence: 0.9965`,
`pages_needing_ocr: [1]` (only the cover) — a document it appears completely
confident about.

**What `.page(n).text` actually returns:** checked directly (fresh
`Document`, no caching, no harness) across 17 pages spread through the book —

| page (1-based) | verdict | confidence | `len(text)` | lines |
| ---: | --- | ---: | ---: | ---: |
| 1 | needs_ocr | 0.00 | 0 | 0 |
| 10 | ok | 1.00 | 746,188 | 7,265 |
| 55 | ok | 1.00 | 536,592 | 9,142 |
| 92 | ok | 1.00 | 677,575 | 7,910 |
| 163 | ok | 1.00 | 753,853 | 6,759 |
| 217 | ok | 1.00 | 752,537 | 6,880 |
| 274 | ok | 1.00 | 23,264 | 546 |

For comparison, the other four extractors return **2,400–3,200 characters**
per page on this same document (measured on the same eight sampled pages;
`pymupdf-raw` avg 3,211, `pymupdf-words` avg 2,562, `tesseract` avg 2,383,
`tesseract-best` avg 2,522). `qalam` returns 200–300x that on every page it
calls `"ok"` and `confidence: 1.00` — every page but the last is not
plausible for a single page of prose. Reading the actual text confirms it is
not garbage: it is the real page content, correct, interleaved with the
document's own running title and page-number footer (`دَليلُك لِلدّراسَاتِ
العُلْيَا بِالخَارِج`, `٩٢ مؤسسة علماء مصر`, …) repeated dozens to hundreds of
times, e.g. the start of page 93 (1-based):

```
دَليلُك لِلدّراسَاتِ العُلْيَا بِالخَارِج - الإصدار الثاني
دَليلُك لِلدّراسَاتِ العُلْيَا بِالخَارِج - الإصدار الثاني
٩٢ مؤسسة علماء مصر
في مجال علوم الحاسب (
دَليلُك لِلدّراسَاتِ العُلْيَا بِالخَارِج - الإصدار الثاني
٩٢ مؤسسة علماء مصر
```

(truncated — this header/footer pair repeats through the whole 677,575-character page)

This did not happen on `ذخائر_لبنان.pdf` — every page checked there (including
page 56 above) came back at a normal, single-page length. So this is a
document-dependent defect in `qalam` 0.1.1's page-text reconstruction —
plausibly triggered by this PDF's repeating header/footer running on every
page — not a universal one, and **`confidence` does not catch it**: every
corrupted page still reports `1.00`. That is the finding worth carrying
forward more than any number in this report: on this version, a high
`qalam` confidence score is not sufficient evidence that `.text` is safe to
index.

## What could not run here

`registry.survey()` in this environment (no API keys, no GPU, no
`tessdata_best` until fetched for this comparison):

| extractor | available | reason |
| --- | --- | --- |
| `pdfplumber` | no | not installed |
| `easyocr` | no | not installed |
| `paddleocr` | no | not installed |
| `surya` | no | `surya-ocr` not installed |
| `qari` | no | transformers/torch not installed |
| `qari-remote` | no | `QARI_REMOTE_URL` not set (no Colab tunnel running) |
| `gemini` | no | `GOOGLE_API_KEY` not set |
| `openrouter` | no | `OPENROUTER_API_KEY` not set |

None of these ran; none of these "lost" to `qalam` — they were simply not
available in this sandbox, per this package's own convention of reporting a
reason rather than a bare failure. `tesseract-best` **did** run here: its
`ara.traineddata` (12.6 MB, from `tessdata_best`) was fetched for this
comparison the same way the Dockerfile fetches it at build time.

## Cost

`qalam.Document(path)` parses a whole PDF once, eagerly; every page after the
first is then a free in-memory lookup. On `ذخائر_لبنان.pdf` (222 pages) that
parse took 0.49 s total — for the *entire book* — against `tesseract-best`'s
1.81 s **per page** (≈ 400 s for the same book). On
`دليلك-...-الثاني.pdf` (274 pages, image-heavy) the parse took ~170 s, still
far below what `tesseract-best` would cost across the same 274 pages
(≈ 8 minutes), and that cost is paid once per document, not per page.

## Recommendation

`qalam` is worth tracking, not adopting yet. On `ذخائر_لبنان.pdf` it matched
or beat `tesseract-best` on both lines checked, scored 4/4 usable on the
automated real-page suite (`pymupdf-raw` and `pymupdf-words` both scored
0/4), and did it from the text layer in a fraction of a second for the whole
book. But the page-text duplication bug on `دليلك-...-الثاني.pdf`, silently
paired with a false `confidence: 1.00`, is disqualifying for production use
of this version without a guard a caller would have to write themselves
(e.g. rejecting a page whose length is a large multiple of the document's
median page length). `OCR_EXTRACTOR` stays `tesseract-best` until either
`qalam` ships a fix or this project adds that guard and re-tests on a larger
sample of real documents.
