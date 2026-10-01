"""Checks that fail loudly before any of the slow work starts."""

from __future__ import annotations

import shutil
from pathlib import Path

import paths  # noqa: F401 - import side effect: puts src/ on sys.path

from application.arabic_extraction.registry import survey


def ensure_tesseract_best_available() -> None:
    """Fail loudly, with the reason, rather than silently building nothing.

    ``registry.build(["tesseract-best"])`` returns an empty list when the
    engine cannot run, which without this check would produce a dataset of
    zero examples and no explanation — the failure this exists to prevent.
    """
    for entry in survey():
        if entry.name == "tesseract-best":
            if entry.ok:
                return
            raise SystemExit(
                "tesseract-best is not available: "
                f"{entry.reason}\n\n"
                "Fetch the model (12.6 MB, same source the Docker image uses) with:\n\n"
                f"  mkdir -p {paths.DEFAULT_TESSDATA_HINT}\n"
                "  curl -fsSL -o "
                f"{paths.DEFAULT_TESSDATA_HINT}/ara.traineddata \\\n"
                "       https://github.com/tesseract-ocr/tessdata_best/raw/main/ara.traineddata\n\n"
                f"then either set TESSDATA_BEST={paths.DEFAULT_TESSDATA_HINT} or pass "
                f"--tessdata-dir {paths.DEFAULT_TESSDATA_HINT}."
            )

    raise SystemExit("tesseract-best is not a known extractor — has arabic_extraction/extractors/ changed?")


def prepare_out_dir(out_dir: Path, force: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        if not force:
            raise SystemExit(
                f"{out_dir} is not empty. Pass --force to regenerate from scratch, "
                "or --out a fresh directory — numbering restarts at 1 either way, "
                "and mixing old and new examples under the same numbers would be silent."
            )
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
