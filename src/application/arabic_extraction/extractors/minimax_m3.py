"""MiniMax-M3 batch benchmark via OpenRouter.

Reads every PDF from a directory, processes them in patches of 250 pages,
sends each page as a base64-encoded PNG image to the minimax/minimax-m3
model on OpenRouter, and saves results per-document under
``src/arabic_extraction/benchmark/results/minimax-m3/``.

Usage (from the src/ directory):

    # Required: set your OpenRouter key
    export OPENROUTER_API_KEY="sk-or-..."

    # Optional: override corpus directory (defaults to ~/Downloads/Testing Rag Docs)
    export OCR_CORPUS="/path/to/your/pdfs"

    python arabic_extraction/extractors/minimax_m3.py
    python arabic_extraction/extractors/minimax_m3.py --corpus /some/other/dir
    python arabic_extraction/extractors/minimax_m3.py --patch-size 50   # fewer pages per patch
    python arabic_extraction/extractors/minimax_m3.py --pages 10        # first N pages only

The script is intentionally self-contained: it imports only from the stdlib
and from modules already on sys.path in this project. No new dependency is
needed.

Patch semantics
---------------
A "patch" here is a consecutive window of up to ``--patch-size`` pages sent
to the model in a *single* API request as separate image_url content parts.
MiniMax-M3's 1M-token context window means 250 pages fit comfortably in one
call. The model returns one transcription block; the script splits it back
into per-page sections by the separator it asks the model to use.

Persistence
-----------
Extracted text is written after each patch completes, so an interrupted run
can be resumed: if a page's output file already exists it is skipped. Timing
and error metadata accumulate in ``results/minimax-m3/<pdf_stem>.json``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The `:free` variant was withdrawn on 2026-09-08 — OpenRouter answers every
# request to it with 404 "This model is unavailable for free. The paid version
# is available now - use this slug instead: minimax/minimax-m3". The paid slug
# costs money per call, so it is not the default: pass --model to choose it
# deliberately, or set OPENROUTER_MODEL.
DEFAULT_MODEL = "minimax/minimax-m3:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Hard cap on pages per call, regardless of size. The real limit is the payload
# budget below -- this only exists so a very low DPI cannot build a patch so
# long that one failure loses an hour of work.
DEFAULT_PATCH_SIZE = 200

# OpenRouter rejects a request body over 30 MB. That, not the model's 1M-token
# window, is what caps a patch: the window would take 250+ pages.
#
# Pages are packed until the *encoded* body would exceed this, so the DPI
# decides the patch size rather than a number guessed in advance -- which is
# the whole point, since halving the DPI roughly quadruples the pages that fit
# and the per-page cost falls with them.
DEFAULT_MAX_MB = 30.0

# Reserved out of that budget for the JSON scaffolding around the images: the
# prompt, the per-image content wrappers, and the data: URI prefixes. Measured
# at a few KB per image, so this is generous on purpose -- a 413 costs the
# whole patch, and the pages that do not fit are simply sent in the next one.
PAYLOAD_OVERHEAD = 0.06

# Where results land — one sub-directory per model, matching the convention in
# src/arabic_extraction/benchmark/results/ (easyocr/, gemini/, tesseract-best/ …).
RESULTS_DIR = Path(__file__).parent.parent / "results" / "minimax-m3"

# The separator the model uses between pages.  Chosen to be unambiguous in
# both Arabic and English, and unlikely to appear in normal body text.
PAGE_SEP = "<<<PAGE_BREAK>>>"

# Prompt sent with every patch.  The separator instruction is what makes
# splitting the response reliable; without it the model either runs pages
# together or chooses its own delimiter.
TRANSCRIBE_PROMPT = (
    "You will receive one or more Arabic document page images in order. "
    "Transcribe all Arabic text exactly as written, preserving the original "
    "word spacing and line breaks. "
    f"After each page's transcription output exactly the string `{PAGE_SEP}` "
    "on its own line, then transcribe the next page. "
    "Output only the transcriptions and the separators — no commentary, "
    "no translation, no markdown fences."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
            from shared.utils.config import get_settings  # type: ignore[import]

            key = getattr(get_settings(), "OPENROUTER_API_KEY", "") or ""
        except Exception:  # noqa: BLE001 — standalone use is expected
            pass
    return key


def _png_bytes(pdf_path: Path, page_number: int, dpi: int = 300) -> bytes:
    """Render one PDF page to PNG bytes at the given DPI."""
    import io

    import pymupdf  # type: ignore[import]
    from PIL import Image  # type: ignore[import]

    with pymupdf.open(pdf_path) as doc:
        pixmap = doc[page_number].get_pixmap(dpi=dpi)

    buf = io.BytesIO()
    Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _call_model(
    key: str,
    images_b64: list[str],
    model: str = DEFAULT_MODEL,
    timeout: int = 900,  # 15 min — a 250-page batch can take 10+ min of inference
) -> tuple[str, dict]:
    """Send a patch of page images; return the raw model text and its usage."""
    import urllib.error
    import urllib.request

    content: list[dict] = [{"type": "text", "text": TRANSCRIBE_PROMPT}]
    for img in images_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img}"},
            }
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        # Scaled to the patch, not a flat ceiling. OpenRouter authorises a
        # request against `max_tokens` up front — it reserves the whole amount
        # against the balance — so a flat 65536 was refused with 402 even for
        # patches whose real output would have been a fraction of it, and even
        # when the account could have afforded the actual work. Roughly 1200
        # tokens covers a dense Arabic A4 page, with a floor for short patches.
        "max_tokens": max(4096, 1200 * len(images_b64)),
    }

    request = urllib.request.Request(
        ENDPOINT,
        json.dumps(payload).encode(),
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/omarTBakr/notebookllm-minus",
            "X-Title": "notebookllm-minus OCR benchmark",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode(errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc

    if body.get("error"):
        raise RuntimeError(f"OpenRouter error: {body['error']}")

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"No choices in response: {str(body)[:300]}")

    # Usage comes back per call and is the only honest way to price a run:
    # a page's cost is dominated by how many tokens its *image* became, which
    # is not something to estimate from the file size.
    return choices[0].get("message", {}).get("content") or "", body.get("usage") or {}


def _split_pages(text: str, expected: int) -> list[str]:
    """Split the model's response back into per-page chunks.

    The model is asked to emit ``PAGE_SEP`` after every page.  We split on
    it and trim.  If the model returns fewer sections than expected (e.g. it
    merged two quiet pages) we pad with empty strings so the index arithmetic
    stays correct.
    """
    parts = [p.strip() for p in text.split(PAGE_SEP)]
    # Drop a trailing empty segment that comes from a trailing separator
    if parts and not parts[-1]:
        parts = parts[:-1]

    # Pad if the model returned fewer sections than pages sent
    while len(parts) < expected:
        parts.append("")

    return parts[:expected]


# ---------------------------------------------------------------------------
# Core: process one PDF
# ---------------------------------------------------------------------------


def _persist(page_dir: Path, batch: list[int], page_texts: list[str], summary: dict, error: str = "") -> None:
    """Write a patch's pages out as soon as it returns.

    Per patch rather than at the end, so an interrupted run keeps everything up
    to the last completed call and a resumed one picks up from there.

    A *failed* patch writes nothing. It used to write an empty file per page,
    which resume then treated as work already done — so the 404s on 2026-09-08
    left 254 empty files that would have made every later run skip those pages
    and report success having read none of them. An error must leave no trace
    on disk, or it silently becomes an answer.
    """
    if error:
        summary["pages"].extend({"page": n, "chars": 0, "empty": True, "error": error} for n in batch)
        return

    for page_num, text in zip(batch, page_texts):
        (page_dir / f"page-{page_num+1:04d}.txt").write_text(text, encoding="utf-8")

        summary["pages"].append({"page": page_num, "chars": len(text), "empty": not text.strip()})


def process_pdf(
    pdf_path: Path,
    results_dir: Path,
    key: str,
    patch_size: int = DEFAULT_PATCH_SIZE,
    max_pages: int | None = None,
    dpi: int = 300,
    max_mb: float = DEFAULT_MAX_MB,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Process every page of *pdf_path* in patches of *patch_size*.

    Returns a summary dict (page counts, timing, errors).
    """
    import pymupdf  # type: ignore[import]

    stem = pdf_path.stem
    page_dir = results_dir / stem
    page_dir.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(pdf_path) as doc:
        total_pages = doc.page_count

    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    print(f"\n{'='*60}")
    print(f"  {pdf_path.name}  ({total_pages} pages, {dpi} dpi, ≤{max_mb} MB per call)")
    print(f"{'='*60}")

    summary: dict = {
        "pdf": str(pdf_path),
        "total_pages": total_pages,
        "patch_size_cap": patch_size,
        "max_mb": max_mb,
        "dpi": dpi,
        "model": model,
        "pages": [],
        "patches": [],
        "errors": [],
    }

    # Pages are packed by *size*, not by count: render one, encode it, and add
    # it to the patch in flight until the next one would push the body over the
    # budget. That is what makes the DPI the lever -- at 150 dpi a page encodes
    # to roughly a quarter of its 300 dpi size, so four times as many ride on
    # one round trip and the fixed per-call cost is divided four ways.
    #
    # A page already on disk is skipped before packing rather than after, so a
    # resumed run fills its patches with work that still needs doing instead of
    # sending pages it already has.
    todo = [n for n in range(total_pages) if not (page_dir / f"page-{n+1:04d}.txt").exists()]

    if not todo:
        print(f"  all {total_pages} pages already done, skipping")

    budget = int(max_mb * 1024 * 1024 * (1 - PAYLOAD_OVERHEAD))

    batch: list[int] = []
    encoded: list[str] = []
    batch_bytes = 0
    render_secs = 0.0

    def send(batch, encoded, render_secs, batch_bytes):
        """One call for the pages packed so far."""
        nonlocal summary

        print(
            f"  pages {batch[0]+1:>4}–{batch[-1]+1:>4}: "
            f"{len(batch):>3} pages, {batch_bytes / 1024 / 1024:5.1f} MB encoded, "
            f"rendered in {render_secs:.1f}s. Calling model …",
            end="",
            flush=True,
        )

        t_call = time.perf_counter()
        try:
            raw, usage = _call_model(key, encoded, model=model)
            call_secs = time.perf_counter() - t_call
            page_texts = _split_pages(raw, len(batch))
            error = ""
        except Exception as exc:  # noqa: BLE001
            call_secs = time.perf_counter() - t_call
            error = f"{type(exc).__name__}: {exc}"
            page_texts = [""] * len(batch)
            usage = {}
            print(f"\n    !! {error}")
            summary["errors"].append({"patch_start": batch[0], "error": error})

        print(f" done ({call_secs:.1f}s, {call_secs / len(batch):.1f}s/page).")

        summary["patches"].append(
            {
                "start_page": batch[0],
                "end_page": batch[-1],
                "pages_in_patch": len(batch),
                "encoded_mb": round(batch_bytes / 1024 / 1024, 2),
                "render_secs": round(render_secs, 2),
                "call_secs": round(call_secs, 2),
                "secs_per_page": round(call_secs / len(batch), 2),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "error": error,
            }
        )

        return page_texts, error

    for page_num in todo:
        t_render = time.perf_counter()
        png = _png_bytes(pdf_path, page_num, dpi=dpi)
        b64 = base64.b64encode(png).decode()
        render_secs += time.perf_counter() - t_render

        # Flush before adding, so the patch that goes out is the last one that
        # fitted. A single page over budget on its own is still sent -- there is
        # nothing smaller to send, and letting the API refuse it is more honest
        # than silently dropping the page.
        if batch and (batch_bytes + len(b64) > budget or len(batch) >= patch_size):
            page_texts, err = send(batch, encoded, render_secs, batch_bytes)
            _persist(page_dir, batch, page_texts, summary, err)
            batch, encoded, batch_bytes, render_secs = [], [], 0, 0.0

        batch.append(page_num)
        encoded.append(b64)
        batch_bytes += len(b64)

    if batch:
        page_texts, err = send(batch, encoded, render_secs, batch_bytes)
        _persist(page_dir, batch, page_texts, summary, err)

    # Write summary JSON
    summary_path = results_dir / f"{stem}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for p in summary["pages"] if not p["empty"])
    print(f"\n  Done. {ok}/{total_pages} pages have text. " f"Results → {page_dir}/")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python arabic_extraction/extractors/minimax_m3.py",
        description="MiniMax-M3 OCR benchmark via OpenRouter, in page patches.",
    )
    default_corpus = Path.home() / "Downloads" / "Testing Rag Docs"
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(os.environ.get("OCR_CORPUS", default_corpus)),
        help=f"Directory of PDFs (default: {default_corpus})",
    )
    parser.add_argument(
        "--patch-size",
        type=int,
        default=DEFAULT_PATCH_SIZE,
        help=f"Hard cap on pages per call; the size budget usually binds first (default: {DEFAULT_PATCH_SIZE})",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip().strip('"').strip("'"),
        help=f"OpenRouter model slug (default: {DEFAULT_MODEL}; the paid one is minimax/minimax-m3)",
    )
    parser.add_argument(
        "--max-mb",
        type=float,
        default=DEFAULT_MAX_MB,
        help=f"Request body budget in MB; OpenRouter refuses over 30 (default: {DEFAULT_MAX_MB})",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Process only the first N pages of each PDF (default: all)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Render resolution (default: 300)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=RESULTS_DIR,
        help=f"Results directory (default: {RESULTS_DIR})",
    )
    args = parser.parse_args(argv)

    key = _key()
    if not key:
        print("ERROR: OPENROUTER_API_KEY is not set.\n" "  export OPENROUTER_API_KEY='sk-or-...'")
        return 1

    if not args.corpus.is_dir():
        print(f"ERROR: corpus directory not found: {args.corpus}")
        return 1

    pdfs = sorted(args.corpus.glob("*.pdf"))
    if not pdfs:
        print(f"No .pdf files found in {args.corpus}")
        return 1

    print(f"Model  : {args.model}")
    print(f"Corpus : {args.corpus}  ({len(pdfs)} PDF(s))")
    print(f"Patch  : ≤{args.max_mb} MB per call, at most {args.patch_size} pages")
    print(f"Output : {args.out}/")

    args.out.mkdir(parents=True, exist_ok=True)

    all_summaries = []
    for pdf in pdfs:
        summary = process_pdf(
            pdf_path=pdf,
            results_dir=args.out,
            key=key,
            patch_size=args.patch_size,
            max_pages=args.pages,
            dpi=args.dpi,
            max_mb=args.max_mb,
            model=args.model,
        )
        all_summaries.append(summary)

    # Aggregate across documents
    total_pages = sum(s["total_pages"] for s in all_summaries)
    total_ok = sum(sum(1 for p in s["pages"] if not p["empty"]) for s in all_summaries)
    total_errors = sum(len(s["errors"]) for s in all_summaries)
    ok_patches = [p for s in all_summaries for p in s["patches"] if not p["error"]]
    all_patch_times = [p["call_secs"] for p in ok_patches]
    avg_call = round(sum(all_patch_times) / len(all_patch_times), 1) if all_patch_times else None
    pages_in_calls = sum(p["pages_in_patch"] for p in ok_patches)
    per_page = round(sum(all_patch_times) / pages_in_calls, 2) if pages_in_calls else None
    avg_pages = round(pages_in_calls / len(ok_patches), 1) if ok_patches else None

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Documents  : {len(all_summaries)}")
    print(f"  Pages      : {total_ok}/{total_pages} with text")
    print(f"  Patch errors: {total_errors}")
    if avg_call is not None:
        print(f"  Avg call   : {avg_call}s / patch, {avg_pages} pages per patch")
        print(f"  Per page   : {per_page}s  <- the number this run exists to measure")

    prompt_tok = sum(p.get("prompt_tokens") or 0 for p in ok_patches)
    completion_tok = sum(p.get("completion_tokens") or 0 for p in ok_patches)

    if prompt_tok or completion_tok:
        # OpenRouter's published rate for minimax-m3 at the time of writing.
        cost = prompt_tok * 0.30e-6 + completion_tok * 1.20e-6
        print(f"  Tokens     : {prompt_tok:,} prompt, {completion_tok:,} completion")
        print(f"  Cost       : ${cost:.4f} total, ${cost / pages_in_calls:.5f} per page")
    print(f"  Results    : {args.out}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
