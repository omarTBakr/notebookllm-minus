"""Run the comparison.

    python -m ocr.benchmark                  # synthetic + real, every available engine
    python -m ocr.benchmark --only tesseract # narrow it
    python -m ocr.benchmark --real-pages 3   # more pages from each real document
    python -m ocr.benchmark --list           # what can run here, and why not

Real documents are picked up from OCR_CORPUS (a directory of PDFs) when it is
set; the synthetic suite always runs, because it is the only part that can
measure accuracy rather than describe output.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..base import Page
from ..registry import survey
from .fixtures import build_all, check_shaping
from .runner import consensus, render_table, run_pages, save

# Results live inside the package, beside the code that produced them: a
# benchmark whose output lands in whatever directory it happened to be invoked
# from is one nobody finds again. Only the virtualenvs holding torch, paddle
# and the rest stay outside the repository — those are the part that would
# actually contaminate it.
RESULTS_DIR = Path(__file__).parent.parent / "results"


def _real_pages(directory: Path, per_document: int) -> list[tuple[Page, None]]:
    """A spread of pages from each PDF, skipping the covers.

    Evenly spaced rather than the first N: the opening pages of a book are a
    title, a colophon and a blank, which tell you nothing about how an engine
    handles the body text that makes up the other 270 pages.
    """
    import pymupdf

    pages: list[tuple[Page, None]] = []

    for pdf in sorted(directory.glob("*.pdf")):
        with pymupdf.open(pdf) as document:
            total = document.page_count

        if not total:
            continue

        step = max(1, total // (per_document + 1))
        chosen = [min(total - 1, step * (i + 1)) for i in range(per_document)]

        pages.extend((Page(pdf, number), None) for number in sorted(set(chosen)))

    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ocr.benchmark")
    parser.add_argument("--only", nargs="*", help="restrict to these extractors")
    parser.add_argument("--real-pages", type=int, default=2, help="pages to sample from each real document")
    corpus = os.environ.get("OCR_CORPUS")
    parser.add_argument("--corpus", type=Path, default=Path(corpus) if corpus else None, help="directory of real PDFs")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="where to write fixtures and the report")
    parser.add_argument("--list", action="store_true", help="show availability and exit")
    parser.add_argument("--skip-synthetic", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        shaping_ok, shaping_reason = check_shaping()
        print(f"Arabic rendering: {'ok' if shaping_ok else shaping_reason}\n")
        for entry in survey():
            print(f"  {entry.name:16} {'available' if entry.ok else 'skip':10} {entry.reason}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)

    # --- synthetic: the only exact measurement -------------------------------
    if not args.skip_synthetic:
        fixtures = build_all(args.out / "fixtures")
        pages = [(Page(f.path, 0), f.truth) for f in fixtures]

        print(f"\n=== synthetic ({len(pages)} pages, ground truth known) ===\n")
        report = run_pages(pages, args.only)

        print(render_table(report.summary()))
        save(report, args.out / "synthetic.json")

        if report.skipped:
            print("\nskipped:")
            for name, reason in report.skipped.items():
                print(f"  {name:16} {reason}")

    # --- real documents: no truth, so shape and agreement --------------------
    if args.corpus and args.corpus.is_dir():
        pages = _real_pages(args.corpus, args.real_pages)

        if pages:
            print(f"\n=== real documents ({len(pages)} pages, no ground truth) ===\n")
            report = run_pages(pages, args.only)

            print(render_table(report.summary()))
            save(report, args.out / "real.json")

            print("\nagreement with the other engines, per page:")
            for run in report.runs:
                scores = consensus(run)
                if scores:
                    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
                    joined = "  ".join(f"{n}={v}" for n, v in ranked)
                    print(f"  {run.source[:28]:30} p{run.page + 1:<4} {joined}")

    print(f"\nfull output written to {args.out}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
