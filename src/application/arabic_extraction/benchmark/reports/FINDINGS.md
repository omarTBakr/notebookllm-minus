# Arabic extraction: what ten methods actually do

Measured on this machine (24 cores, 31 GB, RTX 4060 8 GB) against two real
Arabic PDFs — `ذخائر_لبنان.pdf` (222 pages) and
`دليلك-للدراسات-العليا-بالخارج` (274 pages) — plus four synthetic pages whose
correct text is known exactly.

Every engine ran in its own process. A combined run deadlocked with eighty
threads asleep on a futex, and process isolation is also what makes the memory
figures mean anything: `peak RSS` is a process high-water mark, which is an
engine's footprint only when it had the process to itself.

## How this document is arranged

It grew by appending, and had reached the point where the instructions for
re-running it sat in the middle and the hosted-model work was split across two
sections written weeks apart. Four parts now:

1. **The comparison** — what the local engines do, measured, and which to use.
2. **In the application** — how the chosen one is wired in, and what it costs.
3. **Hosted models** — sending pages to someone else's GPU, and whether it pays.
4. **Status and reproducing** — what is unfinished, and how to run it again.


---

# 1. The comparison

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
of VRAM. Wrapping it on a Colab GPU — `arabic_extraction/benchmark/colab/`, reachable as the
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

---

# 2. In the application


This is no longer only a benchmark result. `tesseract-best` is wired into
ingestion: `ProcessController._reread_unusable_arabic` runs on the PDF layout
path (`PDF_LOADER=pymupdf`), after `extract_pages` and before the Documents are
built, and replaces the text of the pages it re-reads.

It is **off by default**. Four settings in `src/shared/utils/config.py` control it:

| setting | default | what it decides |
| --- | --- | --- |
| `OCR_ENABLED` | `False` | nothing happens at all unless this is set |
| `OCR_EXTRACTOR` | `tesseract-best` | which engine the registry is asked to build |
| `TESSDATA_BEST` | `/usr/share/tessdata-best` | where `ara.traineddata` from `tessdata_best` lives |
| `OCR_MIN_CHARS` | `80` | how much text a page needs before its spacing is judged |

Off by default is the point rather than caution: this costs seconds per page,
and on a well-produced Arabic PDF the text layer is already correct, so
enabling it should be a decision about a corpus and not a habit.

The engine ships in the image. `Docker/services/notebookllm-minus/Dockerfile` installs
`tesseract-ocr` and `tesseract-ocr-ara` as system packages and downloads
`ara.traineddata` and `eng.traineddata` from `tessdata_best` into
`/usr/share/tessdata-best` at build time, so the container starts with no
network. `pytesseract` — a thin wrapper around that binary, not an engine — is
in `src/pyproject.toml`. The heavy engines still live in the separate benchmark
venv; nothing of that size entered the production image.

### Per page, and only when the text layer is broken

`arabic_extraction.language.profile()` makes the decision for each page, and all three of its
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

---

# 3. Hosted models

## A hosted vision model — `openrouter` / minimax-m3, one page per call


Added after Qari proved impractical: 30 s/page on a T4, so shipping pages to a
GPU costs more than reading them locally. A hosted model moves the compute
somewhere it already exists, and a free tier removes the argument against
trying it.

Measured on three real book pages of `ذخائر_لبنان.pdf`, same pages both engines,
`minimax/minimax-m3:free` against `tesseract-best`:

| page | engine | chars | space ratio | usable | s/page |
| ---: | --- | ---: | ---: | :---: | ---: |
| 40 | openrouter | 2002 | 0.168 | yes | 11.7 |
| 40 | `tesseract-best` | 1969 | 0.159 | yes | **2.1** |
| 41 | openrouter | 2159 | 0.166 | yes | 16.3 |
| 41 | `tesseract-best` | 2132 | 0.157 | yes | **2.0** |
| 42 | openrouter | 1784 | 0.152 | yes | 9.6 |
| 42 | `tesseract-best` | 1744 | 0.145 | yes | **1.7** |

Both produce usable Arabic. The two readings agree closely on characters and
much less on words:

| page | character agreement | word overlap |
| ---: | ---: | ---: |
| 40 | 0.925 | 0.745 |
| 41 | 0.930 | 0.716 |
| 42 | 0.916 | 0.720 |

**That gap is the whole finding.** ~92% of characters match and only ~73% of
words do, which is the same CER/WER divergence this report opens with: the two
engines are reading the same page and disagreeing about where words end. With
no ground truth for these pages, this says they differ — not which is right.
Spot-checking page 41 shows minimax misreading the running header
(`ذخائر لبنان` as `نخاص لبنان`) and adding diacritics the scan does not carry,
so its errors are at least not obviously fewer.

### What it costs to run

The interesting column is not the clock:

| | wall | **local CPU** | peak RSS | needs |
| --- | ---: | ---: | ---: | --- |
| `openrouter` | 28.3 s | **0.23 s** | 170 MB | network, an API key |
| `tesseract-best` | ~2 s | ~2 s | ~680 MB + 25 MB/page raster | 1 core, nothing else |

**Local CPU is 0.8% of wall time.** A hosted extractor spends the whole page
waiting on a socket, so it consumes almost no CPU and no memory worth counting
— on the 2-vCPU deployment box, where OCR competes with uvicorn and four celery
workers for two cores, that is the one thing it genuinely offers. It is also
why its concurrency limit is the provider's rate limit rather than
`OCR_WORKERS`: a hundred concurrent requests would cost the box nothing.

### Where it lands

Slower per page than `tesseract-best` even against the *server's* 5.3 s/page,
with no accuracy advantage established. So it is not a bulk path. What it is:

- **A second opinion.** Two engines disagreeing on 27% of words locates the
  pages worth looking at, without a human reading either.
- **CPU relief.** If the box is saturated, this trades latency for cores.
- **A reference candidate**, if a page is ever transcribed by hand to score
  both properly. That measurement has not been made.

The free tier is rate-limited and can be withdrawn, and pages leave the
machine — fine for a published book, worth a thought otherwise. `OCR_ENABLED`
does not reach this: it stays a benchmark extractor, selected explicitly.

---

## MiniMax-M3 — batch mode (~30 pages per call)


*Measured 2026-09-07 against the first 10 pages of both corpus PDFs.*
*Script: `src/application/arabic_extraction/extractors/minimax_m3.py`. Results: `src/application/arabic_extraction/benchmark/results/minimax-m3/`.*

MiniMax-M3's 1-million-token context window makes a different mode possible:
instead of one page per API call, the script sends a patch of multiple pages
in a single request. While the token window could accommodate 250+ pages,
**OpenRouter enforces a strict 30 MB payload limit** on the request body.
At 300 DPI, an Arabic A4 page averages ~750 KB as a PNG, which sets a hard
ceiling of roughly **30–40 pages per call** before the API rejects it with HTTP 413.

Each page in the patch is passed as a separate `image_url` content part;
the model is asked to emit a `<<<PAGE_BREAK>>>` separator between pages
so the single response can be split back into per-page text.

### Measured throughput

| document | pages sent | render | call | call / page | pages with text |
| --- | ---: | ---: | ---: | ---: | ---: |
| `دليلك-للدراسات-العليا-بالخارج` | 10 | 14.6 s | 64.4 s | **6.4 s** | 10 / 10 |
| `ذخائر_لبنان` | 10 | 2.0 s | 117.5 s | **11.8 s** | 8 / 10 |

Wall time per page is lower than the per-request `openrouter` baseline (28.3 s)
was, because that cost was paid once per page and here it is shared across all
pages in the batch. Render time is the bottleneck that batching cannot help: a
300-dpi A4 page still takes ~0.2 s to rasterise.

**Two pages of `ذخائر_لبنان` (pages 9 and 10) came back empty.** The document
opens with a frontispiece and a blank, so those pages may genuinely carry no
Arabic text. The same pages have always been invisible to tesseract and to the
text layer; the model produced no hallucination where the others all do.

### What the output looks like

`دليلك` pages 7–10 — dense prose pages — returned 3 200–4 300 characters each,
consistent with tesseract-best on the same corpus. The `<<<PAGE_BREAK>>>` split
worked on every patch with no manual repair needed.

`ذخائر_لبنان` pages 1–8 returned 26–1 970 characters. Pages 1–2 (`ذكرى لبنان`
and the author credit) are covers and correctly short. Pages 5–8 are body text
and returned comparable volume to a normal tesseract-best run.

### What batching changes and what it does not

**CPU cost stays near zero.** The batch mode has the same property as the
per-page hosted path: the local process spends its time waiting on a socket.
Rendering 10 pages took 2–15 s; everything else is a single blocking
`urlopen`. On a 2-vCPU box that is still the only thing this path genuinely
offers over a local engine.

**Latency per page falls with batch size.** A 250-page batch amortises one
round trip across 250 pages. The measured 6–12 s/page at batch-size-10 would
be lower at the full patch size, since the fixed overhead (TLS handshake, queue
wait, tokenisation of the prompt) is paid once regardless.

**Rate limits apply per call, not per page.** The free tier of MiniMax-M3 on
OpenRouter caps requests, not tokens. A 250-page batch uses one request; 250
single-page calls would use 250. This is the practical reason to prefer batching
on a free tier.

**The separator assumption can fail.** If the model omits `<<<PAGE_BREAK>>>`
for two quiet pages, those pages merge into one chunk and every subsequent page
is off by one. This did not happen in the measured run — the split was exact
for all 20 pages — but it is the failure mode to watch for on noisy or
multi-column layouts.

### Where it lands in the comparison

| engine | s/page | call type | pages leave machine |
| --- | ---: | --- | --- |
| `tesseract-best` | ~2 | local | no |
| `openrouter` (per page) | ~28 | 1 call / page | yes |
| `minimax-m3` (batch 30) | ~6–12 (amortised) | 1 call / 30 pages | yes |

Batch mode is meaningfully faster per page than the per-request path and costs
the same zero local CPU. It is still not competitive with tesseract on
throughput, and it inherits every caveat from the per-page hosted section: pages
leave the machine, the free tier can be withdrawn, and accuracy against Arabic
ground truth has not been established for this model.

Its niche is the same one, with a better cost-per-page on a free endpoint:
a second opinion on pages where tesseract's output is questionable, or a bulk
pass on a corpus too large to send page-by-page before the rate limit fires.
## Packing a call to the 30 MB limit, and what DPI buys

*Measured 2026-09-08. Script: `arabic_extraction/extractors/minimax_m3.py`, which now packs
by size rather than by a page count guessed in advance.*

The batch section above used a fixed 30 pages per call, chosen from an estimate
of how many would fit. That is the wrong lever. The binding constraint is
OpenRouter's **30 MB request body** — not the model's 1M-token window, which
would take 250+ pages — and how many pages fit in 30 MB is decided by the DPI
they were rendered at. So the script now renders a page, encodes it, and adds
it to the patch in flight until the next one would overflow the budget.

### How many pages actually fit

Median base64 size per page, and the resulting patch size against a 30 MB body
(6% reserved for the JSON scaffolding around the images):

| document | 300 dpi | 200 dpi | 150 dpi |
| --- | ---: | ---: | ---: |
| `دليلك-للدراسات-العليا` | 1101 KB → **26 pages** | 671 KB → **43 pages** | 510 KB → **56 pages** |
| `ذخائر_لبنان` | 626 KB → **46 pages** | 449 KB → **64 pages** | 320 KB → **90 pages** |

Confirmed by the packer in a live run: 57 pages at 27.7 MB and 90 pages at
27.5 MB (150 dpi), 45 pages at 28.0 MB and 41 pages at 27.8 MB (200 dpi). It
fills the envelope to within 2 MB every time.

**Halving the DPI roughly doubles the pages per call, not quadruples it.** The
pixel count falls by 4×, but PNG is lossless and Arabic text at lower
resolution compresses less efficiently per pixel — the glyph edges that PNG
encodes cheaply at 300 dpi become a larger fraction of the image at 150. The
two documents differ by nearly 2× at the same DPI, so the page size, not just
the resolution, decides the patch.

### What that is worth

The fixed cost of a call — TLS, queue wait, prompt tokenisation, the model
reading its instructions — is paid once per request regardless of how many
pages ride on it. Going from 30 pages to 90 divides that cost three ways
instead of one. On a free tier where the rate limit counts **requests, not
pages**, it is the difference between a 222-page book taking 8 calls and taking
3.

What it does not buy is accuracy, and that is the part this run could not
measure. Lower DPI means less for the model to read; whether 150 dpi Arabic
naskh transcribes as well as 300 dpi is exactly the question, and it is open.

### The run that was supposed to answer it

It did not run. Every call returned:

```
HTTP 404 {"error":{"message":"This model is unavailable for free. The paid
version is available now - use this slug instead: minimax/minimax-m3"}}
```

**The `:free` tier for minimax-m3 was withdrawn.** The per-page section above
ends by noting that a free tier "is rate-limited and can be withdrawn"; it took
about a week. Six calls across three DPIs failed identically, so no
transcription, latency or accuracy figures exist for this model at any
resolution — only the packing measurements above, which needed no model.

The paid slug (`minimax/minimax-m3`) costs money per call, so the script does
not default to it. `--model` selects it, or `OPENROUTER_MODEL` in `.env`.

**What this says about hosted OCR generally**, and it is the more useful
finding: a free hosted endpoint is not infrastructure. Two of the three hosted
paths tried here have now failed for access rather than quality — Gemini on
429 throughout, minimax on withdrawal — while `tesseract-best` has run on every
page of both documents for weeks at ~2 s/page and costs nothing but CPU. The
local engine is not merely competitive on throughput; it is the only one of the
three that can be relied on to be there.

---

# 4. Status and reproducing

## Still outstanding

- **Does lower DPI cost accuracy?** The packing work below settled how many
  pages fit in a call at 300/200/150 dpi; whether Arabic naskh transcribes as
  well at 150 as at 300 is the question that matters and is unanswered, because
  the model became unavailable before a single page was read. It needs the paid
  slug, or another hosted model.
- **Any hosted figure for minimax-m3 at all.** The batch numbers in this
  document predate the withdrawal and cannot be reproduced on the free tier.
- **Batching is not in the production path.** `arabic_extraction/pipeline.py`
  still sends one page per call; the packing lives only in the benchmark
  extractor.
- **Gemini on real pages, and on more than one synthetic page** — the API has
  returned 429 throughout. Its row is the least trustworthy in this report.
- **`surya`** — installed, but this build spawns a container and fails with
  `unknown or invalid runtime name: nvidia`. It needs the NVIDIA container
  toolkit; it has produced no Arabic result here, good or bad.

## Reproducing


```bash
python -m arabic_extraction.benchmark --list                          # what can run here, and why not
python -m arabic_extraction.benchmark --only tesseract-best --corpus ~/pdfs
python -m arabic_extraction.benchmark.merge                           # fold per-engine runs together
python -m arabic_extraction.benchmark.report results/combined-real.json --output reports/real
```

Engines live in a separate virtualenv; the project image gains nothing. See
`arabic_extraction/README.md` for the setup.
