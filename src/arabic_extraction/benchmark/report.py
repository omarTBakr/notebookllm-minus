"""Generate plots and a requirements-aware Markdown report from benchmark JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..registry import survey


def _plot(report: dict, output: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("install matplotlib to generate OCR plots") from exc

    rows = [row for row in report.get("summary", []) if row.get("ok")]
    if not rows:
        raise RuntimeError("the report contains no successful extractor results")

    names = [row["extractor"] for row in rows]
    figures = {
        "time": ("Median extraction time per page", "seconds", "seconds"),
        "rss": ("Median peak resident memory", "peak_rss_mb", "MiB"),
        "gpu": ("Median peak GPU allocation", "peak_gpu_mb", "MiB"),
        "cpu": ("Median CPU time per page", "cpu_seconds", "seconds"),
        "cer": ("Character error rate", "cer", "CER"),
        "wer": ("Word error rate", "wer", "WER"),
    }

    for filename, (title, field, ylabel) in figures.items():
        values = [row.get(field) for row in rows]
        if not any(value is not None for value in values):
            continue
        values = [value or 0 for value in values]
        figure, axis = plt.subplots(figsize=(10, max(4, len(names) * 0.42)))
        order = sorted(range(len(names)), key=lambda index: values[index])
        axis.barh([names[index] for index in order], [values[index] for index in order])
        axis.set_title(title)
        axis.set_xlabel(ylabel)
        axis.grid(axis="x", alpha=0.25)
        figure.tight_layout()
        figure.savefig(output / f"{filename}.png", dpi=160)
        figure.savefig(output / f"{filename}.svg")
        plt.close(figure)


def _requirements() -> list[dict]:
    return [
        {
            "name": entry.name,
            "available": entry.ok,
            "reason": entry.reason,
            "reads_text_layer": entry.extractor.reads_text_layer,
            "gpu": entry.extractor.wants_gpu,
            "description": entry.extractor.description,
        }
        for entry in survey()
    ]


def _samples(report: dict, limit: int = 2) -> list[str]:
    """The same line of one page, as every engine read it.

    The part of the report that cannot be faked by a metric. Spacing ratios and
    error rates describe output; this shows it, and it is where a reader sees at
    a glance that one path fuses words, another shatters them, a third reverses
    the line, and only some engines produce Arabic anyone could search for.
    """
    lines: list[str] = []

    for run in report.get("runs", [])[:limit]:
        readings = []

        for extraction in run.get("extractions", []):
            text = (extraction.get("text") or "").strip()

            if not text:
                continue

            # The first line long enough to show word structure. Headings and
            # page numbers demonstrate nothing.
            sample = next(
                (line.strip() for line in text.splitlines() if len(line.strip()) > 45),
                text[:90],
            )
            readings.append((extraction["extractor"], sample[:95]))

        if not readings:
            continue

        lines.extend(
            [
                "",
                f"### `{run.get('source', '?')}` page {run.get('page', 0) + 1}",
                "",
            ]
        )

        if run.get("truth"):
            truth = run["truth"].splitlines()[0][:95]
            lines.extend(["Ground truth:", "", "```", truth, "```", ""])

        lines.append("```")
        lines.extend(f"{name:16} {sample}" for name, sample in sorted(readings))
        lines.append("```")

    return lines


def _markdown(report: dict, plot_dir: Path, snapshot_dir: Path | None = None) -> str:
    lines = ["# OCR Benchmark Report", "", "## Comparison", ""]
    lines.append(
        "| extractor | pages | success | CER | WER | seconds/page | CPU seconds | peak RSS MiB | peak GPU MiB | usable |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in report.get("summary", []):

        def cell(key):
            value = row.get(key)
            return "-" if value is None else value

        lines.append(
            f"| {row['extractor']} | {row['pages']} | {row['ok']}/{row['pages']} | "
            f"{cell('cer')} | {cell('wer')} | {cell('seconds')} | "
            f"{cell('cpu_seconds')} | {cell('peak_rss_mb')} | "
            f"{cell('peak_gpu_mb')} | {row['usable']}/{row['ok']} |"
        )

    lines.extend(["", "## Plots", ""])
    lines.extend(
        f"- [{title}](plots/{filename}.png)"
        for filename, title in (
            ("time", "Time"),
            ("cpu", "CPU time"),
            ("rss", "Resident memory"),
            ("gpu", "GPU allocation"),
            ("cer", "CER"),
            ("wer", "WER"),
        )
        if (plot_dir / f"{filename}.png").is_file()
    )

    lines.extend(["", "## Requirements and allocation", ""])
    lines.append("| extractor | available | text layer | GPU | requirements | description |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in _requirements():
        requirements = row["reason"] if not row["available"] else ("GPU" if row["gpu"] else "CPU")
        lines.append(
            f"| {row['name']} | {'yes' if row['available'] else 'no'} | "
            f"{'yes' if row['reads_text_layer'] else 'no'} | "
            f"{'yes' if row['gpu'] else 'no'} | {requirements} | {row['description']} |"
        )

    samples = _samples(report)

    if samples:
        lines.extend(["", "## The same line, every engine", ""])
        lines.append("Metrics describe output; this shows it. Read these before trusting " "any number above them.")
        lines.extend(samples)

    if snapshot_dir is not None and snapshot_dir.is_dir():
        snapshots = sorted(path.name for path in snapshot_dir.iterdir())
        lines.extend(["", "## Snapshots", ""])
        lines.append("Each real page has a rendered image and one text file per engine.")
        lines.extend(f"- [snapshots/{name}](snapshots/{name})" for name in snapshots)

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "CER measures character edits; WER measures retrieval-relevant word edits.",
            "Lower is better for both. Time and CPU seconds are per page. RSS is the",
            "process peak and GPU is the allocator peak when Torch is available; a dash",
            "means the runtime could not expose that metric. Compare synthetic scores",
            "only against other synthetic scores, and use real-document agreement and",
            "manual review for pages without ground truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate(input_path: Path, output: Path) -> Path:
    report = json.loads(input_path.read_text())
    output.mkdir(parents=True, exist_ok=True)
    plot_dir = output / "plots"
    plot_dir.mkdir(exist_ok=True)
    _plot(report, plot_dir)
    markdown = _markdown(report, plot_dir, input_path.parent / "snapshots")
    result = output / "report.md"
    result.write_text(markdown)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("ocr-report"))
    args = parser.parse_args()
    print(generate(args.input, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
