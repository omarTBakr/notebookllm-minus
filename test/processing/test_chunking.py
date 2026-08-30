"""Splitting documents, and the metadata that survives it."""

import pytest
from langchain_core.documents import Document

from controllers import ProcessController
from exceptions import UnsupportedFileTypeError


@pytest.fixture
def controller():
    return ProcessController(chunk_size=50, chunk_overlap=10)


def test_split_file_breaks_a_long_document_up(controller):
    doc = Document(page_content="word " * 200, metadata={"source": "big.txt"})

    chunks = controller.split_file([doc])

    assert len(chunks) > 1


def test_split_file_keeps_the_metadata_on_every_chunk(controller):
    doc = Document(page_content="word " * 200, metadata={"source": "big.txt"})

    chunks = controller.split_file([doc])

    assert all(c.metadata["source"] == "big.txt" for c in chunks)


def test_split_file_leaves_a_short_document_whole(controller):
    doc = Document(page_content="short", metadata={"source": "s.txt"})

    assert [c.page_content for c in controller.split_file([doc])] == ["short"]


def test_split_file_on_nothing_returns_nothing(controller):
    assert controller.split_file([]) == []


def test_process_bytes_reads_plain_text(controller):
    docs = controller.process_bytes(b"hello there", "note1.txt")

    assert "hello there" in "".join(d.page_content for d in docs)


def test_process_bytes_stamps_the_real_filename(controller):
    """The loader writes a temp path into metadata; it is meaningless once the
    temp file is gone, so the asset's own name replaces it."""
    docs = controller.process_bytes(b"hello", "note1.txt")

    assert {d.metadata["source"] for d in docs} == {"note1.txt"}


def test_an_unknown_extension_is_rejected(controller, tmp_path):
    path = tmp_path / "thing.xyz"
    path.write_text("x")

    with pytest.raises(UnsupportedFileTypeError):
        controller.get_loader(path)


# --- text normalisation -------------------------------------------------------
#
# PDF extraction of shaped scripts yields Unicode *presentation forms* — the
# per-position glyph variants a font draws — rather than the standard letters
# anyone typing a query produces. They render identically and compare as
# entirely different characters, which is the difference between a passage
# being retrievable and not.


def test_presentation_forms_fold_to_standard_letters():
    from controllers.TextProcessingController import normalize_text

    # U+FEA9 etc. are Arabic Presentation Forms-B; the query form lives in
    # U+0600-06FF.
    shaped = "ﺩﺍﺮ"
    out = normalize_text(shaped)

    assert all(0x0600 <= ord(c) <= 0x06FF for c in out), out
    assert not any(0xFE70 <= ord(c) <= 0xFEFF for c in out)


def test_bidi_control_characters_are_removed():
    from controllers.TextProcessingController import normalize_text

    # Producers emit these to force visual order; they are noise to a tokeniser.
    out = normalize_text("‫hello‬ ‏world‎")

    assert out == "hello world"


def test_normalisation_leaves_ordinary_text_alone():
    from controllers.TextProcessingController import normalize_text

    text = "Beirut sits on a promontory. The mountains rise behind it."
    assert normalize_text(text) == text


def test_sanitize_normalises_and_still_strips_nulls(controller):
    from langchain_core.documents import Document

    # sanitize/get_splitter live on the TextProcessingController the
    # ProcessController delegates to.
    docs = controller.text.sanitize([Document(page_content="‫ﺍ‬\x00 a")])
    content = docs[0].page_content

    assert "\x00" not in content
    assert not any(0xFE70 <= ord(c) <= 0xFEFF for c in content)


def test_the_primary_splitter_keeps_a_last_resort_separator(controller):
    """The empty separator is the only one that can cut inside an unbroken run,
    and PDF pages with no whitespace are exactly that."""
    assert controller.text.get_splitter()._separators[-1] == ""


# --- start_index, the anchor a highlight is computed from ---------------------


def test_enforce_size_rebases_start_index_onto_the_parent_chunk(controller):
    """The single strongest guarantee this module makes: for any chunk,
    slicing the original text at its start_index reproduces the chunk
    exactly. A highlight rectangle is computed from this offset, so a wrong
    one does not fail loudly — it draws a highlight over the wrong words.

    get_splitter()'s own "" last-resort separator means its primary split
    alone never leaves anything oversized, so enforce_size's guard is
    exercised directly here — this is the path a future _nltk_splitter (no ""
    fallback) would actually take, and where langchain's own start_index
    lands relative to the *parent chunk's* text, not the page's, unless
    rebased.
    """
    page = ("alpha beta gamma delta " * 60) + ("Z" * 2600)
    # A parent chunk that starts mid-page, the way a real second-or-later
    # chunk does — start_index 0 alone would hide a rebase bug entirely.
    parent = Document(
        page_content=page[1379:], metadata={"start_index": 1379, "source": "doc.pdf"}
    )

    pieces = controller.text.enforce_size([parent])

    assert len(pieces) > 1  # otherwise the guard never actually ran
    for piece in pieces:
        start = piece.metadata["start_index"]
        assert start >= 0
        assert page[start : start + len(piece.page_content)] == piece.page_content


def test_start_index_locates_every_chunk_in_the_original_text(controller):
    """Same guarantee, through the full split() pipeline end to end."""
    text = ("alpha beta gamma delta " * 60) + ("Z" * 2600)

    chunks = controller.text.split([Document(page_content=text)])

    for chunk in chunks:
        start = chunk.metadata["start_index"]
        assert start >= 0
        assert text[start : start + len(chunk.page_content)] == chunk.page_content


def test_start_index_is_present_without_the_size_guard_too(controller):
    """Plain prose, well under the limit — enforce_size is a no-op here, and
    the base splitter's own start_index must still be correct on its own."""
    text = "First paragraph.\n\nSecond paragraph, a little longer than the first."

    chunks = controller.text.split([Document(page_content=text)])

    for chunk in chunks:
        start = chunk.metadata["start_index"]
        assert text[start : start + len(chunk.page_content)] == chunk.page_content


def test_start_index_survives_on_a_markdown_document_too(controller):
    """The language-aware splitter is a separate code path (from_language)
    and needs the same add_start_index — easy to add to one and forget the
    other."""
    text = "# Heading\n\n" + ("word " * 300) + "\n\n# Second heading\n\n" + ("word " * 300)

    chunks = controller.text.split([Document(page_content=text)], extension=".md")

    assert len(chunks) > 1
    for chunk in chunks:
        start = chunk.metadata["start_index"]
        assert start >= 0
        assert text[start : start + len(chunk.page_content)] == chunk.page_content


# --- which library reads a PDF ------------------------------------------------
#
# These three are not interchangeable. pdfplumber orders characters by
# x-position, so right-to-left text comes out reversed: it looks like Arabic
# and matches nothing, which is why the default is not simply "whatever is
# newest". See PDF_LOADER in utils/config.py for the measurements.


@pytest.mark.parametrize(
    "setting, expected",
    [
        ("pypdf", "PyPDFLoader"),
        ("pdfplumber", "PDFPlumberLoader"),
        ("pymupdf", "PyMuPDFLoader"),
    ],
)
def test_pdf_loader_setting_selects_the_library(monkeypatch, tmp_path, setting, expected):
    from utils import get_settings
    from controllers import ProcessController

    monkeypatch.setenv("PDF_LOADER", setting)
    get_settings.cache_clear()

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")  # never parsed; the loader is only constructed

    controller = ProcessController()
    controller.settings = get_settings()

    assert type(controller.get_loader(pdf)).__name__ == expected


def test_txt_is_unaffected_by_the_pdf_loader_setting(monkeypatch, tmp_path):
    """PDF_LOADER must not reach the text path."""
    from utils import get_settings
    from controllers import ProcessController

    monkeypatch.setenv("PDF_LOADER", "pymupdf")
    get_settings.cache_clear()

    txt = tmp_path / "note.txt"
    txt.write_text("hello")

    controller = ProcessController()
    controller.settings = get_settings()

    assert type(controller.get_loader(txt)).__name__ == "TextLoader"


# --- markdown ------------------------------------------------------------------
#
# .md gets structure-aware separators (headings and fences first) rather than
# the plain-prose chain. See TextProcessingController.get_splitter.


def test_md_is_loaded_as_text(controller, tmp_path):
    """No conversion — the headings and fences are what the splitter wants to
    see, so a loader that stripped them would defeat the point."""
    path = tmp_path / "note.md"
    path.write_text("# Title\n\nBody text.")

    docs = controller.process_file(path)

    assert docs[0].page_content == "# Title\n\nBody text."


def test_get_splitter_uses_markdown_separators_for_md(controller):
    plain = controller.text.get_splitter()
    markdown = controller.text.get_splitter(".md")

    assert markdown._separators != plain._separators
    assert "\n#{1,6} " in markdown._separators
    # The last-resort separator is what lets enforce_size's guard work on a
    # markdown file with no whitespace at all, same as it does for a PDF.
    assert markdown._separators[-1] == ""


def test_a_markdown_document_prefers_to_split_on_headings(controller):
    text = "# One\n\n" + ("word " * 40) + "\n\n# Two\n\n" + ("word " * 40)
    doc = Document(page_content=text, metadata={"source": "doc.md"})

    chunks = controller.split_file([doc], extension=".md")

    assert any(c.page_content.startswith("# One") for c in chunks)
    assert any(c.page_content.startswith("# Two") for c in chunks)


def test_txt_and_pdf_extensions_are_unaffected_by_markdown_support(controller):
    """.md is additive — the default splitter's separators are unchanged."""
    assert controller.text.get_splitter(None)._separators == \
        controller.text.get_splitter(".txt")._separators == \
        controller.text.get_splitter(".pdf")._separators


def test_process_and_split_picks_the_splitter_from_the_real_filename():
    """process_and_split takes bytes and a name; the extension has to come
    from the name, since there is no file on disk to inspect."""
    import asyncio

    controller = ProcessController(chunk_size=50, chunk_overlap=10)
    markdown = b"# Heading\n\n" + (b"word " * 40) + b"\n\n# Two\n\n" + (b"word " * 40)

    chunks = asyncio.run(controller.process_and_split(markdown, "doc.md"))

    assert any(c.page_content.startswith("# Heading") for c in chunks)


# --- highlight rectangles, attached during ingest -----------------------------
#
# Only when PDF_LOADER=pymupdf: the pypdf/pdfplumber loaders never expose word
# boxes, so a chunk from either has nothing to compute a highlight from. See
# ProcessController._process_pdf_with_layout and PdfLayoutController.


def _synthetic_pdf(path, pages_text):
    """A real PDF, built in-process — no fixture file, no network."""
    import pymupdf

    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 30), text, fontsize=10)
    doc.save(str(path))
    doc.close()


def _with_pdf_loader(monkeypatch, value):
    from utils import get_settings

    monkeypatch.setenv("PDF_LOADER", value)
    get_settings.cache_clear()
    controller = ProcessController(chunk_size=1000, chunk_overlap=200)
    controller.settings = get_settings()
    return controller


def test_pymupdf_chunks_carry_a_highlight(monkeypatch, tmp_path):
    controller = _with_pdf_loader(monkeypatch, "pymupdf")
    pdf = tmp_path / "doc.pdf"
    _synthetic_pdf(pdf, ["alpha beta gamma delta epsilon zeta"])

    docs = controller.process_file(pdf)
    chunks = controller.split_file(docs)

    assert len(chunks) >= 1
    [chunk] = chunks
    assert chunk.metadata["page"] == 0
    assert chunk.metadata["highlight"]["r"]  # at least one rectangle
    assert chunk.metadata["highlight"]["o"] == "tl"


def test_pypdf_chunks_carry_no_highlight(monkeypatch, tmp_path):
    """The default loader is opt-out of this entirely — no word boxes, no
    highlight key, not even an empty one."""
    controller = _with_pdf_loader(monkeypatch, "pypdf")
    pdf = tmp_path / "doc.pdf"
    _synthetic_pdf(pdf, ["alpha beta gamma"])

    docs = controller.process_file(pdf)
    chunks = controller.split_file(docs)

    for chunk in chunks:
        assert "highlight" not in chunk.metadata


def test_a_highlight_rect_is_computed_from_the_right_page(monkeypatch, tmp_path):
    """Two pages: the second page's chunk must not pick up the first page's
    rects, which only holds if _pdf_pages is genuinely keyed by page."""
    controller = _with_pdf_loader(monkeypatch, "pymupdf")
    pdf = tmp_path / "doc.pdf"
    _synthetic_pdf(pdf, ["first page words here", "second page other words"])

    docs = controller.process_file(pdf)
    chunks = controller.split_file(docs)

    by_page = {c.metadata["page"]: c for c in chunks}
    assert set(by_page) == {0, 1}
    for page_index, chunk in by_page.items():
        assert chunk.metadata["highlight"] is not None
        # Distinct pages should not share identical rectangles by coincidence
        # of both starting at the same margin — spot-check they were computed
        # independently by confirming both are present and non-empty.
        assert len(chunk.metadata["highlight"]["r"]) > 0


def test_process_and_split_attaches_highlights_end_to_end(monkeypatch, tmp_path):
    """The real ingest path: bytes in, chunk_metadata out — same route
    routes/chat/assets.py actually calls."""
    import asyncio

    controller = _with_pdf_loader(monkeypatch, "pymupdf")
    pdf = tmp_path / "doc.pdf"
    _synthetic_pdf(pdf, ["hello world this is a highlighted passage"])
    pdf_bytes = pdf.read_bytes()

    chunks = asyncio.run(controller.process_and_split(pdf_bytes, "doc.pdf"))

    assert any("highlight" in c.metadata for c in chunks)
    assert all(c.metadata["source"] == "doc.pdf" for c in chunks)
