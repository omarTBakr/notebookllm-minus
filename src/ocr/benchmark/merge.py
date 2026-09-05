"""Fold the per-engine result directories back into one benchmark JSON.

Engines are benchmarked one process at a time. That is not tidiness: a combined
run deadlocked with eighty threads asleep on a futex after several PyTorch-based
engines were loaded into one interpreter, and paddle, surya and qari each want
mutually incompatible pins. Isolation also makes the memory numbers honest —
`peak_rss_mb` is a process high-water mark, which is the engine's footprint only
when the engine had the process to itself.

The cost is that results arrive as one directory per engine. This merges them
into the single shape `report.py` consumes, so the plots and the comparison
table cover every engine that ran without either module knowing the runs were
separate.

    python -m ocr.benchmark.merge                    # writes results/combined-<suite>.json
    python -m ocr.benchmark.merge --suite synthetic
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "results"


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def merge(results: Path, suite: str) -> dict:
    """Combine every `<engine>/<suite>.json` under *results* into one report."""
    summary: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    runs: dict[tuple[str, int], dict] = {}

    # Oldest first, so a later run overwrites an earlier one. Ordering by name
    # instead put `easyocr-fixed` (a 2-page smoke test) after the full 4-page
    # rerun purely because of the alphabet, and the report showed the smaller
    # run. Recency is what "a rerun supersedes" actually means.
    directories = [p for p in results.iterdir() if p.is_dir()]
    directories.sort(key=lambda d: ((d / f"{suite}.json").stat().st_mtime if (d / f"{suite}.json").is_file() else 0))

    for directory in directories:
        payload = _load(directory / f"{suite}.json")

        if payload is None:
            continue

        for name, reason in payload.get("skipped", {}).items():
            skipped.setdefault(name, reason)

        for row in payload.get("summary", []):
            name = row["extractor"]

            # A later successful run supersedes an earlier failed one: reruns
            # happen because something was fixed, and the fixed result is the
            # one that describes the engine.
            if row.get("ok") or name not in summary:
                summary[name] = row
                skipped.pop(name, None)

        for run in payload.get("runs", []):
            key = (run.get("source", ""), run.get("page", 0))
            merged = runs.setdefault(
                key,
                {
                    "source": run.get("source", ""),
                    "source_path": run.get("source_path", ""),
                    "page": run.get("page", 0),
                    "truth": run.get("truth"),
                    "extractions": [],
                },
            )

            seen = {e["extractor"] for e in merged["extractions"]}

            for extraction in run.get("extractions", []):
                if extraction["extractor"] in seen and not extraction.get("text"):
                    continue
                merged["extractions"] = [e for e in merged["extractions"] if e["extractor"] != extraction["extractor"]]
                merged["extractions"].append(extraction)

    # An engine that produced a row anywhere is not "skipped", whatever a later
    # directory said. Popping inside the loop was not enough: running
    # `--only tesseract` reports every *other* engine as unavailable in that
    # process, so a run that happened afterwards re-added engines an earlier
    # run had scored — and the report then claimed both that an engine could
    # not run and what it scored.
    for name in summary:
        skipped.pop(name, None)

    ordered = sorted(
        summary.values(),
        key=lambda row: (row.get("wer") is None, row.get("wer") or 0, row["extractor"]),
    )

    return {
        "suite": suite,
        "skipped": skipped,
        "summary": ordered,
        "runs": [runs[key] for key in sorted(runs)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ocr.benchmark.merge")
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    parser.add_argument("--suite", choices=("real", "synthetic", "both"), default="both")
    args = parser.parse_args(argv)

    suites = ("real", "synthetic") if args.suite == "both" else (args.suite,)

    for suite in suites:
        combined = merge(args.results, suite)

        if not combined["summary"]:
            print(f"{suite}: nothing to merge")
            continue

        destination = args.results / f"combined-{suite}.json"
        destination.write_text(json.dumps(combined, ensure_ascii=False, indent=2))

        engines = ", ".join(row["extractor"] for row in combined["summary"])
        print(
            f"{suite}: {len(combined['summary'])} engine(s) over " f"{len(combined['runs'])} page(s) -> {destination}"
        )
        print(f"  {engines}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
