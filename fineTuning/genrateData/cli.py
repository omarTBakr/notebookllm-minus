"""Argument parsing and the run loop, wiring the other modules together."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import paths

from application.arabic_extraction.registry import build

from extract import corrected_pages, damaged_pages
from grouping import group_pages
from preflight import ensure_tesseract_best_available, prepare_out_dir
from writer import write_examples, write_manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the Arabic OCR-correction fine-tuning dataset for gemma4:e4b. "
            "See fineTuning/README.md for the full design and rationale."
        )
    )
    parser.add_argument(
        "--pdf",
        action="append",
        type=Path,
        default=None,
        help=f"a PDF to process (repeatable). Default: every PDF under {paths.DEFAULT_ASSETS}",
    )
    parser.add_argument(
        "--out", type=Path, default=paths.DEFAULT_OUT, help=f"output root (default: {paths.DEFAULT_OUT})"
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=paths.DEFAULT_MIN_CHARS,
        help=f"pages whose damaged text is shorter than this are merged forward (default: {paths.DEFAULT_MIN_CHARS})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="process at most this many pages per PDF — for a fast smoke test before a full run",
    )
    parser.add_argument("--lang", default="ara", help="tesseract language code (default: ara)")
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        default=None,
        help="directory holding ara.traineddata from tessdata_best. "
        "Overrides TESSDATA_BEST for this run.",
    )
    parser.add_argument("--force", action="store_true", help="wipe --out first if it already has content")

    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    if args.tessdata_dir is not None:
        os.environ["TESSDATA_BEST"] = str(args.tessdata_dir)

    ensure_tesseract_best_available()
    prepare_out_dir(args.out, args.force)

    pdf_paths = args.pdf or sorted(paths.DEFAULT_ASSETS.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"No PDFs given and none found under {paths.DEFAULT_ASSETS}")

    extractor = build(["tesseract-best"], **{"tesseract-best": {"lang": args.lang}})[0]
    extractor.warm_up()

    manifest: list[dict] = []
    next_id = 1
    started = time.perf_counter()

    for pdf_path in pdf_paths:
        print(f"== {pdf_path.name} ==")

        damaged = damaged_pages(pdf_path)
        total = len(damaged)
        limit = total if args.max_pages is None else min(total, args.max_pages)
        damaged = damaged[:limit]

        def progress(done: int, page_limit: int = limit, seconds: float = 0.0) -> None:
            print(f"  tesseract-best: page {done}/{page_limit} ({seconds:.1f}s)")

        corrected = corrected_pages(pdf_path, extractor, limit, on_progress=progress)

        groups = group_pages(damaged, corrected, args.min_chars)
        entries = write_examples(groups, pdf_path.name, args.out, start_at=next_id)

        next_id += len(entries)
        manifest.extend(entries)

        print(f"  {len(entries)} example(s) from {limit} of {total} page(s)")

    write_manifest(manifest, args.out)

    elapsed = time.perf_counter() - started
    print(f"\n{len(manifest)} example(s) written to {args.out} in {elapsed:.0f}s")


def main(argv: list[str] | None = None) -> None:
    run(parse_args(argv))
