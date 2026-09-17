# Qari-OCR on a Colab GPU, reachable over ngrok

`qari_server.ipynb` runs Qari-OCR behind an HTTP API on a free Colab GPU and
exposes it through an ngrok tunnel, so this project — running on a 2-vCPU box
with no GPU — can send it a PDF and get Arabic text back.

## Why

Measured against this project's own corpus (`ocr/reports/FINDINGS.md`):

| | WER | s/page (real book page) | needs |
| --- | ---: | ---: | --- |
| `qari` | **0.063** | ~30 (RTX 4060; a T4 will be slower) | 5 GB VRAM |
| `tesseract-best` | 0.172 | 2.3 on 2 cores | nothing |

Qari is roughly **13× slower per page** than `tesseract-best` and nearly 3× more
accurate. That trade is worth making for a document you care about and not for a
274-page book you are ingesting in bulk.

It keeps diacritics and handles inline English, which everything else in the
comparison mangles — and it cannot run on the deployment target at all. A Colab
GPU is the cheapest way to have both: `tesseract-best` inline for everything,
Qari for the documents worth re-reading properly.

The first half of that is no longer hypothetical. `tesseract-best` is wired
into ingestion behind `OCR_ENABLED`, per page and only where the Arabic text
layer is unusable — see `ocr/README.md` and `ocr/reports/FINDINGS.md`. This
notebook is the other half, and it stays a workbench: a Colab session and a
free ngrok hostname are not something ingestion should depend on.

## Step by step

**1. Open the notebook in Colab.** Upload `qari_server.ipynb` at
<https://colab.research.google.com> (*File → Upload notebook*), or push it to a
GitHub repo and open it with *File → Open notebook → GitHub*.

**2. Turn the GPU on.** *Runtime → Change runtime type → T4 GPU → Save.* Do this
**before** running anything. Cell 1 stops the notebook if there is no GPU, because
this model on CPU is not slow — it is unusable.

**3. Get an ngrok authtoken.** Sign up free at <https://ngrok.com>, copy the token
from <https://dashboard.ngrok.com/get-started/your-authtoken>. Without one the
tunnel closes after a couple of minutes.

**4. Run cell 2 (install), then check its output.** If it prints
`transformers was already imported in this kernel`, do *Runtime → Restart session*
and start again from cell 1. Colab preloads its own `transformers`; skipping the
restart is the most common way this notebook fails, and it fails later, inside
the model load, looking like something else entirely.

**5. Edit cell 3.** Paste the ngrok token, and invent a long random secret:

```python
NGROK_AUTHTOKEN = "2abc...your token..."
API_SECRET      = "a-long-random-string-you-invent"
```

The secret is not decoration. An ngrok URL is on the public internet; without it
anyone who finds the address can spend your GPU and read what they upload.

**6. Run cells 4 to 7 in order.** Cell 4 downloads ~5 GB on the first run. Cell 7
prints the public URL:

```
======================================================================
  https://xxxx-yy-zz.ngrok-free.app
======================================================================
```

**7. Check it (cell 8).** `/health` should answer, and an unauthenticated `/ocr`
should be refused with 401.

**8. Send a PDF (cell 9).** Drag a file into the file browser on the left, set
`PDF_PATH`, run. For anything large, mount Drive instead.

**9. Leave cell 10 running and keep the tab open.** Colab stops an idle notebook
after ~90 minutes and every session at ~12 hours.

## Calling it from elsewhere

```bash
curl -X POST "$QARI_URL/ocr" \
  -H "Authorization: Bearer $QARI_SECRET" \
  -F "file=@book.pdf" \
  -F "first_page=0" -F "last_page=9"
```

```python
import requests

response = requests.post(
    f"{QARI_URL}/ocr",
    headers={"Authorization": f"Bearer {QARI_SECRET}"},
    files={"file": open("book.pdf", "rb")},
    data={"first_page": 0, "last_page": 9},
    timeout=600,
)
for page in response.json()["pages"]:
    print(page["page"], page["text"][:200])
```

**Budget about 30 seconds per page.** A 10-page batch is ~5 minutes and a
20-page batch ~10, so set the client timeout in minutes, not seconds — the
example above uses 600s for good reason. The server caps a request at 20 pages.

(The benchmark's 3.3 s/page figure is for small synthetic test pages. A dense
book page at 300 dpi is an order of magnitude more work, and that is the number
that matters here.)

## Pointing the app at it

The same conditional per-page path that runs `tesseract-best` can send its
pages here instead — `qari-remote` is an extractor like any other:

```bash
OCR_ENABLED=true
OCR_EXTRACTOR=qari-remote
QARI_REMOTE_URL=https://xxxx-yy-zz.ngrok-free.app
QARI_REMOTE_SECRET=the-secret-from-cell-3
QARI_REMOTE_TIMEOUT=120     # optional; 120 s per page by default
```

These have to be in the **process environment** — `Settings` has no
`QARI_REMOTE_*` fields, and the extractor reads `os.environ` directly. Under
compose that means `env_file`, which is the same environment either way.

Two things follow from the numbers above rather than from preference:

- **It is still per page and still conditional.** Only Arabic pages whose text
  layer is unusable are sent, so a healthy PDF sends nothing. At ~30 s/page a
  book that fails the check throughout will take hours, and the notebook caps a
  request at 20 pages anyway.
- **A dead tunnel is a warning, not a failed upload.** Availability is decided
  by an actual `/health` call, so an expired Colab session makes the extractor
  unavailable; ingestion logs the reason and keeps the text layer.

An OCR'd page loses its citation highlight — the offsets no longer match the
word boxes the page's text was built from — and that is true of this engine as
much as of `tesseract-best`. `ocr/reports/FINDINGS.md` has the reasoning.

## The limits, before you build on it

- **The URL changes on every restart.** Free ngrok gives a random hostname, and
  the Colab session it points at is temporary. Anything calling this needs the
  URL as configuration it can be told again, not a constant.
- **Sessions end.** ~12 hours maximum, ~90 minutes idle. This is a workbench, not
  a service. If ingestion depends on it, ingestion breaks when the session does.
- **Documents leave your machine** — to Google, and through ngrok. Fine for a
  published book; think twice for anything else. `tesseract-best` runs locally
  and costs 0.17 WER instead of 0.06.
- **One request at a time.** The GPU is not shared and there is no queue; two
  concurrent callers will contend.

## If it breaks

| symptom | cause |
| --- | --- |
| `No GPU` at cell 1 | runtime type is still CPU |
| model load fails oddly | `transformers` upgraded without a restart — see step 4 |
| `ERR_NGROK_108` | another tunnel is already open on the free plan; cell 7 closes stale ones, or kill them in the ngrok dashboard |
| 401 from `/ocr` | the `Authorization: Bearer <secret>` header is missing or wrong |
| 413 | over the 50 MB upload cap; split the PDF |
| client timeout | asking for too many pages at once — batch by 10–20 |
| tunnel dead, no error | the Colab session expired; re-run and redistribute the new URL |
