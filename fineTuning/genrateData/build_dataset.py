#!/usr/bin/env python3
"""Build the Arabic OCR-correction fine-tuning dataset for gemma4:e4b.

Entry point only — the actual work is split across this directory:

    paths.py       repo-root/src resolution; every other module needs this
                   imported first so src/ lands on sys.path
    extract.py     the two extractors: pymupdf (damaged) and
                   tesseract-best (corrected)
    grouping.py    pairing pages into training examples, merging short ones
    writer.py      one numbered directory + conversation.json per example
    preflight.py   fail fast if tesseract-best can't run, or --out is dirty
    cli.py         argument parsing and the run loop tying it together

See fineTuning/README.md for the design, the exact JSON schema, and why each
of these pieces is shaped the way it is.

Usage: python fineTuning/genrateData/build_dataset.py [--help]
"""

from __future__ import annotations

import paths  # noqa: F401 - import side effect: puts src/ on sys.path

from cli import main

if __name__ == "__main__":
    main()
