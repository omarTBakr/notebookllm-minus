# Arabic extraction: what ten methods actually do

Measured on this machine (24 cores, 31 GB, RTX 4060 8 GB) against two real
Arabic PDFs — `ذخائر_لبنان.pdf` (222 pages) and
`دليلك-للدراسات-العليا-بالخارج` (274 pages) — plus four synthetic pages whose
correct text is known exactly.

Every engine ran in its own process. A combined run deadlocked with eighty
threads asleep on a futex, and process isolation is also what makes the memory
figures mean anything: `peak RSS` is a process high-water mark, which is an
engine's footprint only when it had the process to itself.

## The headline

**OCR of the rendered page beats the PDF's own text layer on this corpus.**
That is not the expected answer, and it is why the comparison was worth running
rather than reasoning about. These files are not scans — they have text layers.
The text is simply wrong.

## Exact accuracy — synthetic pages, ground truth known

| engine | CER | WER | s/page | cpu s | peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qari` | **0.033** | **0.063** | 3.29 | 3.51 | 5232 MB (+4756 MB GPU) |
| `gemini` | 0.108 | 0.167 | 0.99 | 0.09 | 687 MB |
| `tesseract-best` | 0.130 | 0.172 | **0.26** | **0.09** | **681 MB** |
| `easyocr` | 0.099 | 0.316 | 0.42 | 0.50 | 1343 MB (+1444 MB GPU) |
| `tesseract` | 0.316 | 0.545 | 0.20 | 0.09 | 682 MB |
| `paddleocr` | 0.433 | 0.680 | 16.09 | 96.08 | 13208 MB |
| text-layer methods | — | — | — | — | (synthetic pages carry no text layer) |

**The `s/page` column here is not a page cost.** These are small synthetic
fixtures holding a few lines of known text; a dense book page at 300 dpi is an
order of magnitude more work. `qari` reads a synthetic fixture in 3.29 s and a
real page in 33.9 s. The real-document table below is the one to quote.

Gemini's row is one page of four; the rest were refused by the free-tier quota,
so it is not a fair comparison — it is the best available reference reading, on
a quarter of the sample.

**Qari's number is only this good because a measurement bug was fixed.** It is
trained to emit document structure as HTML, so every `<h1>` and `<u>` counted
as a word the reference did not contain: measured raw it scored 0.233 WER while
its Arabic was letter-perfect. Stripping the markup — which the ingestion path
would do anyway — moved it from 0.233 to 0.063. A separate bug had capped its
vision tokens so low that a dense book page came back as five characters.

**Read CER and WER together, and prefer WER.** EasyOCR has the best character
error rate of anything measured — and the second-worst word error rate among
engines that worked. It recognises characters well and segments words badly,
which for retrieval is the failure that matters: a word split in two is a word
no query will match, even though every letter of it survived.

That divergence is the single most useful thing this benchmark produced, and a
CER-only comparison would have ranked EasyOCR first.

## Real documents — no ground truth, so shape and cost

| engine | usable pages | spaces/char | s/page | cpu s | peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qari` | **4/4** | 0.173 | 33.92 | 34.31 | 5232 MB (+5456 MB GPU) |
| `tesseract` | **4/4** | 0.175 | 1.52 | 0.70 | 800 MB |
| `tesseract-best` | **4/4** | 0.175 | 2.69 | 0.70 | 798 MB |
| `easyocr` | **4/4** | 0.181 | 3.89 | 5.32 | 1565 MB (+2907 MB GPU) |
| `pdfplumber` | 2/4 | 0.218 | 0.15 | 0.15 | 677 MB |
| `paddleocr` | 2/4 | 0.216 | **127.23** | 344.78 | **22266 MB** |
| `pymupdf-words` | 1/4 | 0.246 | 0.46 | 0.46 | 651 MB |
| `pymupdf-raw` | **0/4** | 0.115 | 0.47 | 0.46 | 649 MB |

Healthy Arabic prose runs 0.13–0.22 spaces per character. Both text-layer paths
sit outside it, in opposite directions.

## The same line, read seven ways

From `دليلك-للدراسات-العليا-بالخارج`, page 92. The correct reading is
*"دليلك للدراسات العليا بالخارج - الإصدار الثاني"*:

```
qari             دَلِيلُك لِلدَراسَاتِ العُلْيَا بِالخَارِج - الإصدار الثاني 92
tesseract-best   ذدَليلك لِلدَراسَاتٍ العْليا بالخَارج - الإصدار الثاني
tesseract        دَليئُك لِلدَراسَاتٍ العْلْيَا بِالخَارِجٍ - الإصدار الثاني
pymupdf-raw      ي اإلصدار الثا- ‮ َد لي ُلك ِلل ّد را َس ا ِت ال ُع ْل َيا ِبال َخ ا ِرج‬
pymupdf-words    اإلصدار الثا - لي ُلك ِلل را ا ال ْل َيا ِبال ا ِرج
pdfplumber       Conference) تاﺮﻤﺗﺆﻤﻟا تارﻮﺸ ﻤـ ﺴ ﺎﻣ ﺪﺟﻮﻳ ...
paddleocr        يم يه ي لو  يد لمO ل ل ي ول  ع ي ي  ل يقلو و ل   ل ىو
```

Three engines read the title, and only `qari` gets every diacritic right — it
also picks up the page number and reads inline English (`Conference (Computer
Science)`) correctly elsewhere on the page. `pymupdf-raw` fuses and misorders,
`pymupdf-words` drops letters entirely, `pdfplumber` returns presentation-form
glyphs from a different part of the page, and `paddleocr` — after 127 seconds
and 22 GB — returns noise.

No metric in this package would tell you that as clearly as looking.

## Recommendation

**On the 2-vCPU server: `tesseract-best`, triggered by the text-layer check.**
**If a GPU is available: `qari`.** They are not close on quality and not close
on cost, and which one wins is decided entirely by the hardware. The first half
of that is now what the application does — see *In the application*, below.

Measured on this machine, pinned to two cores to match the deployment target:

| | `tesseract-best` | `qari` |
| --- | --- | --- |
| WER (exact) | 0.172 | **0.063** |
| s/page, 24 cores | 2.13 | 33.9 |
| s/page, **2 cores** | **2.29** | not runnable — needs a GPU |
| memory | 800 MB | 5.2 GB + 5.5 GB VRAM |

`tesseract-best` loses only 7% going from 24 cores to 2, because it is
effectively single-threaded (0.42 CPU-seconds either way). That is the number
that matters for a 2-vCPU box, and wall-clock on a 24-core machine would have
hidden it.

Qari is the better reader by a factor of nearly three on word error rate, reads
inline English correctly, and preserves diacritics — but it needs a GPU and 5 GB
of VRAM. Wrapping it on a Colab GPU — `ocr/colab/`, reachable as the
`qari-remote` extractor — is a reasonable way to have both: `tesseract-best`
inline for everything, Qari for documents worth re-reading properly.

- It matches Gemini's word error rate (0.172 vs 0.167) at zero marginal cost,
  no API key, and no data leaving the machine.
- 2.29 s/page pinned to two cores, ~800 MB, no GPU.
- The model is the differentiator: distributions ship the *fast* traineddata
  (1.4 MB), and `tessdata_best` (12.6 MB) cuts WER from 0.545 to 0.172 — a
  three-fold improvement for an 11 MB download. Benchmarking "Tesseract" on the
  distro model is how it gets its reputation for being hopeless at Arabic.

Do not OCR every page. `language.profile()` costs microseconds and answers
whether the text layer is usable; on a well-produced Arabic PDF it is, and
re-reading it would be seconds per page spent to make the text worse.

## In the application

This is no longer only a benchmark result. `tesseract-best` is wired into
ingestion: `ProcessController._reread_unusable_arabic` runs on the PDF layout
path (`PDF_LOADER=pymupdf`), after `extract_pages` and before the Documents are
built, and replaces the text of the pages it re-reads.

It is **off by default**. Four settings in `src/utils/config.py` control it:

| setting | default | what it decides |
| --- | --- | --- |
| `OCR_ENABLED` | `False` | nothing happens at all unless this is set |
| `OCR_EXTRACTOR` | `tesseract-best` | which engine the registry is asked to build |
| `TESSDATA_BEST` | `/usr/share/tessdata-best` | where `ara.traineddata` from `tessdata_best` lives |
| `OCR_MIN_CHARS` | `80` | how much text a page needs before its spacing is judged |

Off by default is the point rather than caution: this costs seconds per page,
and on a well-produced Arabic PDF the text layer is already correct, so
enabling it should be a decision about a corpus and not a habit.

The engine ships in the image. `Docker/notebookllm-minus/Dockerfile` installs
`tesseract-ocr` and `tesseract-ocr-ara` as system packages and downloads
`ara.traineddata` and `eng.traineddata` from `tessdata_best` into
`/usr/share/tessdata-best` at build time, so the container starts with no
network. `pytesseract` — a thin wrapper around that binary, not an engine — is
in `src/pyproject.toml`. The heavy engines still live in the separate benchmark
venv; nothing of that size entered the production image.

### Per page, and only when the text layer is broken

`ocr.language.profile()` makes the decision for each page, and all three of its
answers must agree before a page is re-read:

- the page is **Arabic** — decided from the codepoints, not by a detector;
- its text layer is **unusable** — spacing outside the healthy 0.13–0.22 band,
  which is the fragmented (`ا ليسا ر`) and run-together (`وحدثفي`) failures this
  report measured;
- it has at least `OCR_MIN_CHARS` characters, so a plate, a chapter heading or
  a mostly-blank page is not re-read on the strength of a ratio computed over
  nothing.

Everything else keeps what the PDF gave. That is not a performance compromise:
a healthy text layer *is* the characters the author typed, and OCR trades those
for a guess at the pixels. On the pages where the text layer works, re-reading
it would be seconds per page spent to make the text worse.

### An OCR'd page keeps an approximate citation highlight

A citation's rectangles come from `highlight_metadata`, which maps character
offsets in a chunk onto the per-word bounding boxes the page's text was built
from. Replacing that text with OCR output means the offsets address a
different string, so they cannot be used unchanged.

This was first resolved by dropping the page from `_pdf_pages` so its chunks
carried no highlight at all, on the reasoning that a highlight pointing at the
wrong sentence is worse than none. That reasoning was too pessimistic about
what the boxes still say. OCR returns **no coordinates whatsoever**, so those
boxes remain the only positional information about the page that exists, and
both strings read the same page in the same order.

So the page is kept. `_reread_unusable_arabic` records
`len(page.text) / len(result.text)` in `_ocr_scale`, `split_file` passes it to
`highlight_metadata(..., scale=...)`, and the resulting metadata carries
`"approx": 1`. Chunks are a fixed size and cover a good fraction of a page, so
landing in the right region is what this needs to do — and a reader can tell an
exact highlight from a close one, which is what the flag is for.

Verified end to end on a 222-page Arabic book: 378 of 378 chunks carried a
highlight, 376 of them marked approximate (the two exact ones being the pages
whose text layer was healthy enough not to be re-read).

### A missing engine is a warning, not a failure

If `OCR_ENABLED` is set but the named extractor cannot run — no `tesseract`
binary, `TESSDATA_BEST` unset, no `ara.traineddata` under it — the registry's
`available()` reason is logged as a warning and the text layer is kept for
those pages. The upload succeeds. Failing a document over a missing OCR binary
is a worse outcome than indexing imperfect text, and the reason is in the log
rather than in a 500.

Per-page OCR failures behave the same way: the page is logged and keeps its
original text (and, having not been replaced, keeps its highlight).

### What it costs, per real page

| | `tesseract-best` | `qari` |
| --- | --- | --- |
| s/page, real book pages | 2.69 | 33.92 |
| s/page, **pinned to 2 cores** | **2.29** | not runnable — needs a GPU |
| s/page, small synthetic fixture | 0.26 | 3.29 |

(The pinned row is the separate two-core run above, which measured 2.13 s/page
on 24 cores and 2.29 on two — 7% apart, because Tesseract is effectively
single-threaded.)

The last row is what the exact-accuracy table reports, and it is not a budget
for a real page. Quote 2.3–2.7 s/page for `tesseract-best` and ~30–34
s/page for `qari`; the 3.29 s figure describes a few lines of rendered text.

Only pages that fail the profile pay this. On a well-produced Arabic PDF that
is no pages at all, and the cost is the microseconds `profile()` spends
deciding.

## Not recommended, and why

- **`paddleocr`** — 127 s/page, 22 GB, and unreadable output. It also needs
  `enable_mkldnn=False` on this build or it raises
  `ConvertPirAttribute2RuntimeAttribute` from inside its executor.
- **`easyocr`** — best CER, but the word segmentation makes it worse than
  `tesseract-best` for retrieval, at 1.4× the time and a GPU.
- **`pdfplumber`** — reverses right-to-left text. Already documented in the
  project's own `PdfLoader` enum; this confirms it.
- **`gemini`** — the accuracy yardstick, and rate-limited to the point of being
  unmeasurable here (3 of 4 synthetic pages and all 4 real pages refused).
  Viable only with billing enabled, and it sends documents off the machine.

## Still outstanding

- **Gemini on real pages, and on more than one synthetic page** — the API has
  returned 429 throughout. Its row is the least trustworthy in this report.
- **`surya`** — installed, but this build spawns a container and fails with
  `unknown or invalid runtime name: nvidia`. It needs the NVIDIA container
  toolkit; it has produced no Arabic result here, good or bad.

## Reproducing

```bash
python -m ocr.benchmark --list                          # what can run here, and why not
python -m ocr.benchmark --only tesseract-best --corpus ~/pdfs
python -m ocr.benchmark.merge                           # fold per-engine runs together
python -m ocr.benchmark.report results/combined-real.json --output reports/real
```

Engines live in a separate virtualenv; the project image gains nothing. See
`ocr/README.md` for the setup.
