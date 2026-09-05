"""Measuring the engines, kept out of the application's way.

Nothing the running application imports lives here. `ocr.base`, `ocr.language`,
`ocr.pipeline` and `ocr.registry` are the library; this subpackage is the
harness that compares extractors against each other, and it pulls in matplotlib,
Pillow rendering and whole OCR stacks that the production image does not have.

    python -m ocr.benchmark --list        # what can run here, and why not
    python -m ocr.benchmark               # synthetic + real, every engine
    python -m ocr.benchmark.merge         # fold per-engine runs together
    python -m ocr.benchmark.report <json> # plots and a Markdown report

Importing this package is deliberately not enough to run anything: the modules
are imported by the entry points that need them, so `import ocr` stays cheap.
"""
