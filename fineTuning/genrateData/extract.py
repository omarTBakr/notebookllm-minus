"""The two extractors this dataset pairs up: pymupdf (damaged) and
tesseract-best (corrected).

See fineTuning/README.md for why these two specifically, and why the pairing
is per-page rather than per-arbitrary-chunk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import paths  # noqa: F401 - import side effect: puts src/ on sys.path

from langchain_community.document_loaders import PyMuPDFLoader

from application.arabic_extraction.base import Page


def damaged_pages(pdf_path: Path) -> list[str]:
    """The 'user' side: one damaged (pymupdf) text per page, in page order.

    LangChain's PyMuPDFLoader, exactly as named in the request — plain
    ``page.get_text()``, no word-box reconstruction and no normalisation.
    ``mode="page"`` (its default) gives one Document per page whose metadata
    carries a 0-based ``page`` number — the same 0-based numbering
    ``application.arabic_extraction.base.Page`` uses, so the two sides line up with no reindexing.

    Left exactly as PyMuPDFLoader returns it: normalising away the damage
    here would defeat the point of a *correction* dataset.
    """
    docs = PyMuPDFLoader(str(pdf_path)).load()
    return [doc.page_content for doc in docs]


def corrected_pages(pdf_path: Path, extractor, num_pages: int, on_progress=None) -> list[str]:
    """The 'model' side: one corrected (tesseract-best) text per page.

    Reuses ``application.arabic_extraction.base.Page`` and the extractor's own ``.run()``, which times
    the call and turns a failure into ``Extraction.error`` rather than
    raising — one bad page (a torn scan, a blank plate) must not lose the
    rest of the document.

    A page can also come back *not ok* with no ``.error`` at all — tesseract
    ran cleanly and simply recognised nothing (measured on this corpus: page 8
    of one source PDF). That case still gets a line on stderr, or an operator
    watching the run has no way to tell a genuinely blank page from one this
    script silently dropped.
    """
    texts: list[str] = []

    for number in range(num_pages):
        page = Page(pdf_path, number)
        try:
            extraction = extractor.run(page)
            if extraction.ok:
                texts.append(extraction.text)
            else:
                texts.append("")
                print(
                    f"    page {number}: tesseract-best produced nothing usable "
                    f"({extraction.error or 'no text recognised'})",
                    file=sys.stderr,
                )
        finally:
            page.close()

        if on_progress is not None:
            on_progress(number + 1, num_pages, extraction.seconds)

    return texts
