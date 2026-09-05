"""Extractors that read the PDF's own text rather than the rendered page.

These are the baseline every OCR engine has to beat to be worth its cost. They
are effectively free — milliseconds against seconds — and on a well-produced PDF
they are also *exactly* right, because they return the characters the author
typed rather than a guess at what the pixels say.

Three of them, because this project's two extraction paths disagree on Arabic
and the disagreement is the reason this package exists:

`pymupdf-raw` asks pymupdf for the page's text and takes what it gives.

`pymupdf-words` rebuilds the text from per-word bounding boxes, which is what
PdfLayoutController does — it needs the boxes to highlight a citation on the
page, and it builds the text from the same tokens so the text a chunk was cut
from and the boxes its highlight uses cannot drift apart.

That second one is where Arabic breaks. Word boxes come from glyph positions,
and Arabic's non-joining letters (ا ر و د ز ذ) leave a real gap in the middle of
a word, so the splitter cuts there: `اليسار` becomes `ا ليسا ر`. The first path
has the opposite failure on the same corpus — producers that emit no space
glyphs at all, running clauses together as `وحدثفي`.

`pdfplumber` is included as a third opinion where installed, since it segments
words by its own rules and may fail differently from both.
"""

from __future__ import annotations

from ..base import ArabicExtractor, Page


class PyMuPDFRawExtractor(ArabicExtractor):
    name = "pymupdf-raw"
    description = "pymupdf get_text() — the page's own text layer, untouched"
    reads_text_layer = True

    def _extract(self, page: Page) -> str:
        return page.text_layer


class PyMuPDFWordsExtractor(ArabicExtractor):
    name = "pymupdf-words"
    description = "rebuilt from per-word boxes — what PdfLayoutController does"
    reads_text_layer = True

    def _extract(self, page: Page) -> str:
        import pymupdf

        document = pymupdf.open(page.path)

        try:
            words = document[page.number].get_text("words")
        finally:
            document.close()

        if not words:
            return ""

        # x0, y0, x1, y1, word, block_no, line_no, word_no — group back into
        # lines by (block, line) so the output is comparable with the raw path
        # rather than one long ribbon of tokens.
        lines: dict[tuple[int, int], list[str]] = {}

        for word in words:
            key = (word[5], word[6])
            lines.setdefault(key, []).append(word[4])

        return "\n".join(" ".join(tokens) for _, tokens in sorted(lines.items()))


class PdfPlumberExtractor(ArabicExtractor):
    name = "pdfplumber"
    description = "pdfplumber's own word segmentation — a third opinion"
    reads_text_layer = True

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            return False, "pdfplumber is not installed"
        return True, ""

    def _extract(self, page: Page) -> str:
        import pdfplumber

        with pdfplumber.open(page.path) as document:
            return document.pages[page.number].extract_text() or ""
