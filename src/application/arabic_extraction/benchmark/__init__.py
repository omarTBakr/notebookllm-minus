"""Measuring the engines, kept out of the application's way.

Nothing the running application imports lives here. `arabic_extraction.base`, `arabic_extraction.language`,
`arabic_extraction.pipeline` and `arabic_extraction.registry` are the library; this subpackage is the
harness that compares extractors against each other, and it pulls in matplotlib,
Pillow rendering and whole OCR stacks that the production image does not have.

    python -m arabic_extraction.benchmark --list        # what can run here, and why not
    python -m arabic_extraction.benchmark               # synthetic + real, every engine
    python -m arabic_extraction.benchmark.merge         # fold per-engine runs together
    python -m arabic_extraction.benchmark.report <json> # plots and a Markdown report

Importing this package is deliberately not enough to run anything: the modules
are imported by the entry points that need them, so `import arabic_extraction` stays cheap.
"""
