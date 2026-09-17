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
| qari-remote | no | no | no | QARI_REMOTE_URL is not set (start arabic_extraction/benchmark/colab/qari_server.ipynb) | Qari-OCR on a remote GPU (Colab + ngrok), over HTTP |
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

**The whole-document verdict, for the record.** `doc.confidence` over all 222
pages is 0.973, and `doc.pages_needing_ocr` is `[1, 2, 6, 38, 221, 222]` — six
pages (front matter, plates or blanks, by their position), none of them
mid-book prose. No page here shows the duplication behaviour described for
the other document below.

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
(`qalam.Document(path)`) took 159–173 s across repeated runs and reported
`confidence: 0.996–0.9965`, `pages_needing_ocr: [1]` (only the cover) — a
document it appears completely confident about.

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
page 56 above) came back at a normal, single-page length.

**Full census, not a sample.** The 17-page spot check above was extended to
every page of the document (`qalam` 0.1.1, installed via `pip install qalam`
and re-run directly against `.page(n)` for `n` in `1..=274`):

| | pages | share |
| --- | ---: | ---: |
| normal-sized (`< 5,000` chars) | 1 | 0.4% |
| bloated (`>= 5,000` chars) | 273 | 99.6% |

The one normal-sized page is the cover (`len == 0`, `verdict == needs_ocr`,
`confidence == 0.0` — correctly flagged). Every other page in the book is
bloated: mean 626,429 characters, max 977,748 (one page, nearly a million
characters), and **every single one of the 273 bloated pages reports
`confidence == 1.0`** — not a range, not "mostly," the literal set of
distinct confidence values among them is `{1.0}`. So this is not a
document-dependent edge case affecting a handful of unlucky pages: it is
this document's dominant behaviour, and `confidence` is uniformly useless
as a signal for it, not just occasionally wrong.

**The guard this report originally proposed does not work.** The
Recommendation below (in its first version) suggested "rejecting a page
whose length is a large multiple of the document's median page length" as a
caller-side mitigation. Tested against the real numbers: the document's own
median page length *is* a bloated one (~725,000 characters, since 99.6% of
pages are bloated), so a multiple-of-median check flags **zero** of the 273
corrupted pages — checked directly, `pages >10x median length: 0 / 274`. A
relative threshold cannot distinguish corrupted from healthy when corruption
is the majority case. A workable guard needs an *absolute* bound instead —
e.g. comparing against a fast independent extractor's page length
(`pymupdf-raw` averaged 3,211 characters/page on this same document) rather
than trusting anything `qalam` reports about itself for this document.

So this is a document-dependent defect in `qalam` 0.1.1's page-text
reconstruction — plausibly triggered by this PDF's repeating header/footer
running on every page — not a universal one (`ذخائر_لبنان.pdf` shows none of
it), but where it does trigger, it is not a minority-of-pages problem, and
**`confidence` does not catch it on a single page of the 273 affected**.
That is the finding worth carrying forward more than any number in this
report: on this version, a high `qalam` confidence score is not sufficient
evidence that `.text` is safe to index, and neither is comparing a page
against the rest of its own document.

No existing GitHub issue on [misraj-ai/qalam](https://github.com/misraj-ai/qalam/issues)
describes this (checked issues #1 and #5, and the findings log in `PLAN.md`
§10, which documents a *different*, already-ruled-out duplication concern on
another test file) — worth filing upstream with this document, per the
project's own "attach the PDF if you can" contribution note.

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

## Cost — CPU only, and the "per-page" number is not one number

`qalam` is a Rust extension with **zero Python dependencies**
(`pip show qalam` — `Requires:` is empty) and no GPU code path anywhere in
its API surface: it parses the PDF's own object graph and content streams,
the same class of work as `pymupdf`, not an ML model. Confirmed empirically
here too — `qalam.Document(path)` pins one CPU core near 100% for the
duration of a parse and allocates no GPU memory (`peak_gpu_mb` is `-` in the
Comparison table above, the same as every other CPU-only extractor). Its own
README adds a detail worth knowing for a threaded caller: the GIL is
released during extraction, so it does not block other Python threads while
it runs.

`qalam.Document(path)` parses a whole PDF once, eagerly; every page after the
first is then a free in-memory lookup — re-measured directly here at
0.0009–0.21 s for *every remaining page combined*, i.e. well under a
millisecond per page once the document is parsed. So "per page" is
misleading for the one cost that matters: the eager parse, paid once per
*document*, not once per *page*:

| document | pages | parse (whole doc) | ≈ ms/page if averaged | `tesseract-best` for the same book |
| --- | ---: | ---: | ---: | ---: |
| `ذخائر_لبنان.pdf` | 222 | 0.7 s | 3.2 | 1.81 s/page → ≈ 400 s |
| `دليلك-...-الثاني.pdf` | 274 | 159–161 s | ≈ 585–590 | 1.81 s/page → ≈ 496 s |

The two documents differ by two orders of magnitude in parse time for a
similar page count — `دليلك` is 25 MB and image-heavy against `ذخائر`'s
13 MB, so the cost tracks document complexity, not a fixed per-page rate.
Even at its slowest measured here, `qalam` is still faster than
`tesseract-best` would be across the same book, and unlike `tesseract-best`
that cost is paid once at load time rather than once per page re-read.

## Recommendation

`qalam` is worth tracking, not adopting yet. It is CPU-only (a Rust PDF
parser, not a model — no GPU code path, no Python dependencies) and parses a
whole document once rather than paying a per-page cost: 0.7 s for
`ذخائر_لبنان.pdf`'s 222 pages, 159–161 s for `دليلك-...-الثاني.pdf`'s 274
image-heavy pages, both still cheaper than `tesseract-best` would be across
the same books. On `ذخائر_لبنان.pdf` it matched or beat `tesseract-best` on
both lines checked, scored 4/4 usable on the automated real-page suite
(`pymupdf-raw` and `pymupdf-words` both scored 0/4), and its whole-document
verdict (0.973 confidence, 6 pages flagged for OCR, none mid-book) held up.

But the page-text duplication bug on `دليلك-...-الثاني.pdf` is not a
minority-of-pages defect: a full census (not a sample) found 273 of 274
pages affected, every one of them reporting `confidence: 1.0`, and it
disqualifies the guard this report first proposed — rejecting a page whose
length is a large multiple of the document's own median fails here, because
the median itself is a corrupted page when 99.6% of the document is
corrupted. A workable guard needs an absolute bound (or a cross-check
against a fast independent extractor like `pymupdf-raw`), not a
relative-to-this-document one. `OCR_EXTRACTOR` stays `tesseract-best` until
either `qalam` ships a fix — worth raising upstream, since no existing issue
or `PLAN.md` finding describes this — or this project adds that guard and
re-tests on a larger sample of real documents.
