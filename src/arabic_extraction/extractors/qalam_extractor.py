"""qalam — reading-order Arabic text extraction, without OCR.

Same family as `text_layer.py`: it reads the PDF's own text rather than
re-rendering the page, so it is priced the same way — milliseconds, not
seconds. What makes it worth its own file rather than a fourth entry in
`text_layer.py` is that it is somebody else's answer to the exact problem
this package exists to measure: `pymupdf-raw` fuses words together,
`pymupdf-words` shatters them at every non-joining letter, and `pdfplumber`
gets whole lines reversed (see `arabic_extraction/README.md`). qalam's own pitch is fixing
precisely that, without paying tesseract's per-page cost.

It ships as Rust with published Python bindings — `pip install qalam`, no
build toolchain needed — and its API is a thin, already-parsed object rather
than a stream: `qalam.Document(path)` extracts every page up front and hands
back typed pages, confidence, and which pages it thinks need OCR instead.
That last part is a second opinion this package doesn't otherwise have: every
other text-layer extractor here just returns whatever it produced, right or
wrong, and `language.profile()` has to infer usability after the fact from
spacing. qalam says so directly, per page — `needs_ocr` and `reasons` — which
is worth reading even though the harness (`ArabicExtractor._extract` returns
only text) has nowhere to put it. See `QalamExtractor.inspect` for that
information outside the benchmark loop.

**Known issue (qalam 0.1.1, found on this corpus).** `.page(n).text` can come
back 200-300x too long on some documents — the real page content, correct,
with the document's own running header/footer repeated dozens to hundreds of
times — while `.confidence` still reports 1.0 for the affected page. Not
observed on every document (`ذخائر_لبنان.pdf` was clean throughout; see
`reports/qalam/report/report.md`), so it is not filtered out here: doing that
silently would hide a real upstream bug behind extractor code that looks like
it works. A caller with a length budget should treat a page many times the
document's median length as suspect regardless of what `.confidence` says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import ArabicExtractor, Page

if TYPE_CHECKING:  # pragma: no cover - typing only
    import qalam


class QalamExtractor(ArabicExtractor):
    """Wraps `qalam.Document` — parsed once per path and reused per page.

    `qalam.Document(path)` extracts the whole document eagerly on
    construction (its own docstring: "so every property below is a plain
    lookup that cannot fail"), while the harness asks one page at a time and
    usually asks for every page of the same file before moving to the next
    one. Reparsing the whole PDF on every page would multiply the cost by the
    page count for no reason, so the parsed document is cached by path.
    """

    name = "qalam"
    description = "qalam — logical reading-order text-layer extraction, no OCR"
    reads_text_layer = True

    #: Path (as a string) -> parsed qalam.Document. Class-level, like the
    #: model caches on the OCR engines below, so every instance in one
    #: process shares the parse rather than repeating it.
    _documents: dict[str, "qalam.Document"] = {}

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import qalam  # noqa: F401
        except ImportError:
            return False, "qalam is not installed (pip install qalam)"
        return True, ""

    @classmethod
    def _document(cls, path) -> "qalam.Document":
        import qalam

        key = str(path)

        if key not in cls._documents:
            cls._documents[key] = qalam.Document(key)

        return cls._documents[key]

    def _extract(self, page: Page) -> str:
        # qalam counts pages from 1; the harness counts from 0, as pymupdf does.
        document = self._document(page.path)
        return document.page(page.number + 1).text

    @classmethod
    def inspect(cls, path) -> dict:
        """qalam's own verdict for a document: confidence, and which pages it
        would send to OCR.

        Not part of the `ArabicExtractor` contract — the harness only ever
        asks for text — but worth surfacing separately, since qalam is the
        one extractor here that says *which* pages it does not trust rather
        than leaving that to `language.profile()` to guess.
        """
        document = cls._document(path)

        return {
            "confidence": document.confidence,
            "pages_needing_ocr": document.pages_needing_ocr,
            "pages": len(document),
        }
