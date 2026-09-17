"""Runs every available extractor over the same pages and reports the result.

Two suites, because they answer different questions and neither is sufficient.

The **synthetic** suite renders pages from known strings, so CER and WER are
exact. It is the only place accuracy can be *measured* rather than inferred, and
the only defence against a vision model that produces confident, fluent Arabic
that was never on the page.

The **real** suite runs the same engines over actual documents. There is no
ground truth, so it reports the intrinsic signals from `metrics` plus
cross-engine agreement, and its job is to show which engines degrade on real
scans, real fonts and real layout after doing well on clean renderings.

Timing is reported per page with model loading excluded — every extractor is
warmed up before the clock starts, or a VLM's first page would carry a
thirty-second model load and look a hundred times slower than it is.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ..base import Extraction, Page
from ..registry import build, survey
from .metrics import Score, agreement, score, word_overlap


@dataclass
class PageRun:
    """Every extractor's attempt at one page."""

    source: str
    source_path: str
    page: int
    truth: str | None
    extractions: list[Extraction] = field(default_factory=list)
    scores: list[Score] = field(default_factory=list)


@dataclass
class Report:
    runs: list[PageRun] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    def by_extractor(self) -> dict[str, list[Score]]:
        grouped: dict[str, list[Score]] = {}

        for run in self.runs:
            for entry in run.scores:
                grouped.setdefault(entry.extractor, []).append(entry)

        return grouped

    def summary(self) -> list[dict]:
        """One row per extractor, aggregated across pages."""
        rows = []

        for name, scores in self.by_extractor().items():
            worked = [s for s in scores if not s.failed]
            cers = [s.cer for s in worked if s.cer is not None]
            wers = [s.wer for s in worked if s.wer is not None]

            rows.append(
                {
                    "extractor": name,
                    "pages": len(scores),
                    "ok": len(worked),
                    # Median, not mean: one pathological page (a plate, a blank)
                    # otherwise decides the ranking for an engine that is fine
                    # on everything else.
                    "cer": round(statistics.median(cers), 4) if cers else None,
                    "wer": round(statistics.median(wers), 4) if wers else None,
                    "seconds": round(statistics.median([s.seconds for s in scores]), 2),
                    "cpu_seconds": _median_optional(scores, "cpu_seconds"),
                    "peak_rss_mb": _median_optional(scores, "peak_rss_mb"),
                    "rss_delta_mb": _median_optional(scores, "rss_delta_mb"),
                    "peak_gpu_mb": _median_optional(scores, "peak_gpu_mb"),
                    "space_ratio": (round(statistics.median([s.space_ratio for s in worked]), 3) if worked else None),
                    "usable": sum(1 for s in worked if s.usable),
                    "errors": [s.error for s in scores if s.error][:1],
                }
            )

        # Rank by WER where it exists — it is the metric that reflects whether
        # retrieval will work, since a fragmented word is a lost word even when
        # every character survived.
        rows.sort(key=lambda r: (r["wer"] is None, r["wer"] if r["wer"] is not None else 0))

        return rows


def run_pages(pages: list[tuple[Page, str | None]], names: list[str] | None = None, **options) -> Report:
    """Run every available extractor over every page."""
    report = Report()

    for entry in survey():
        if not entry.ok:
            report.skipped[entry.name] = entry.reason

    extractors = build(names, **options)

    # Warm up once, before anything is timed.
    for extractor in extractors:
        try:
            extractor.warm_up()
        except Exception as exc:  # noqa: BLE001
            report.skipped[extractor.name] = f"warm-up failed: {type(exc).__name__}: {exc}"

    extractors = [e for e in extractors if e.name not in report.skipped]

    for page, truth in pages:
        run = PageRun(
            source=Path(page.path).name,
            source_path=str(page.path),
            page=page.number,
            truth=truth,
        )

        for extractor in extractors:
            extraction = extractor.run(page)
            run.extractions.append(extraction)
            run.scores.append(score(extraction, truth))

        report.runs.append(run)
        page.close()

    return report


def consensus(run: PageRun) -> dict[str, float]:
    """How much each extractor agrees with the others on a page.

    The fallback where there is no ground truth. An engine that agrees with
    nothing else is either uniquely right or uniquely wrong, and either way is
    the one to read by eye.
    """
    working = [e for e in run.extractions if e.ok]

    if len(working) < 2:
        return {}

    result = {}
    for extraction in working:
        others = [o for o in working if o.extractor != extraction.extractor]
        result[extraction.extractor] = round(
            statistics.mean(
                max(agreement(extraction.text, o.text), word_overlap(extraction.text, o.text)) for o in others
            ),
            3,
        )

    return result


def render_table(rows: list[dict]) -> str:
    """The summary as a markdown table."""
    if not rows:
        return "_no results_"

    # Cost sits beside quality on purpose. The deployment target is a 2-vCPU
    # box, so an engine that wins on WER and needs a GPU has not won anything
    # here — and CPU-seconds is the column that says so, because wall-clock
    # flatters anything that parallelises across 24 cores it will not have.
    header = "| extractor | ok | CER | WER | s/page | cpu s | RSS MB | GPU MB | " "spaces/char | usable |"
    divider = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"

    def cell(value, digits=None):
        if value is None:
            return "—"
        return round(value, digits) if digits is not None else value

    lines = [header, divider]
    for row in rows:
        lines.append(
            f"| {row['extractor']} | {row['ok']}/{row['pages']} | "
            f"{cell(row['cer'])} | {cell(row['wer'])} | {row['seconds']} | "
            f"{cell(row.get('cpu_seconds'), 2)} | {cell(row.get('peak_rss_mb'), 0)} | "
            f"{cell(row.get('peak_gpu_mb'), 0)} | "
            f"{cell(row['space_ratio'])} | {row['usable']}/{row['ok']} |"
        )

    return "\n".join(lines)


def save(report: Report, path: Path) -> None:
    """Write the full report, extractions included, for reading afterwards."""
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir = path.parent / "snapshots"
    snapshot_dir.mkdir(exist_ok=True)

    for run in report.runs:
        stem = f"{Path(run.source).stem}-p{run.page + 1}"
        try:
            page = Page(Path(run.source_path), run.page, dpi=150)
            page.image.save(snapshot_dir / f"{stem}.png")
            page.close()
        except Exception:
            pass
        for extraction in run.extractions:
            text_path = snapshot_dir / f"{stem}-{extraction.extractor}.txt"
            text_path.write_text(extraction.text, encoding="utf-8")

    payload = {
        "skipped": report.skipped,
        "summary": report.summary(),
        "runs": [
            {
                "source": run.source,
                "source_path": run.source_path,
                "page": run.page,
                "truth": run.truth,
                "consensus": consensus(run),
                "extractions": [
                    {
                        "extractor": e.extractor,
                        "seconds": round(e.seconds, 3),
                        "cpu_seconds": e.cpu_seconds,
                        "peak_rss_mb": e.peak_rss_mb,
                        "peak_gpu_mb": e.peak_gpu_mb,
                        "error": e.error,
                        "text": e.text,
                    }
                    for e in run.extractions
                ],
            }
            for run in report.runs
        ],
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _median_optional(scores: list[Score], field_name: str) -> float | None:
    values = [getattr(score, field_name) for score in scores]
    values = [value for value in values if value is not None]
    return round(statistics.median(values), 2) if values else None
