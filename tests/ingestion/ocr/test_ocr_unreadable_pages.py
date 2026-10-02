"""OCR for pages with no usable text layer: which pages, and what is kept.

Separate from `test_ocr_integration.py`, which covers re-reading badly *spaced*
Arabic. These pages had nothing to re-read -- a scan, a picture of a title, a
cover -- and before this pass were indexed as empty.
"""

from pathlib import Path

import pytest

from application.services import ProcessService

OCR_ARABIC = "في بعض الأماكن من تلك السهول مما غير بعض الشيء في وضعها فرأينا أن نلخص منها ما يتعلق بمطلبنا"


@pytest.fixture
def controller(monkeypatch):
    controller = ProcessService()
    monkeypatch.setattr(controller.settings, "OCR_UNREADABLE_PAGES", True)
    return controller


@pytest.fixture
def flagged(monkeypatch):
    """qalam's verdict, fixed: pages 2 and 4 have no usable text layer."""
    from application.arabic_extraction.extractors.qalam_extractor import QalamExtractor

    pages = [2, 4]
    monkeypatch.setattr(
        QalamExtractor, "available", classmethod(lambda cls: (True, ""))
    )
    monkeypatch.setattr(
        QalamExtractor,
        "pages_needing_ocr",
        classmethod(lambda cls, path, start, end: pages),
    )
    return pages


@pytest.fixture
def tesseract(monkeypatch):
    """A fake tesseract-best, so nothing here needs tessdata_best installed."""
    from application.arabic_extraction.base import ArabicExtractor

    class Fake(ArabicExtractor):
        name = "tesseract-best"
        calls: list = []
        text: dict = {}

        def _extract(self, page):
            Fake.calls.append((page.number, self.options.get("lang")))
            return Fake.text.get(page.number, OCR_ARABIC)

    Fake.calls = []
    Fake.text = {}
    monkeypatch.setattr("application.arabic_extraction.registry.ALL_EXTRACTORS", (Fake,))
    return Fake


def test_only_the_pages_qalam_flags_are_read(controller, flagged, tesseract, tmp_path):
    replacements = controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10)

    assert sorted(number for number, _ in tesseract.calls) == [2, 4]
    assert sorted(replacements) == [2, 4]


def test_arabic_and_english_are_read_together(controller, flagged, tesseract, tmp_path):
    """A scanned page is as likely to be the English one, and the Arabic model
    alone turns English into garbage."""
    controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10)

    assert {lang for _, lang in tesseract.calls} == {"ara+eng"}


def test_a_picture_is_not_indexed_as_text(controller, flagged, tesseract, tmp_path):
    """What tesseract really returned for ذخائر_لبنان.pdf's cover photograph."""
    tesseract.text = {
        2: '©. L ©. ©. U ©. [1] [ J Cc 0" a REE pn. SIAC Ph 7 بي ل Sho 1 PUI EQN Zo'
    }

    replacements = controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10)

    assert sorted(replacements) == [4]


def test_a_blank_page_stays_empty(controller, flagged, tesseract, tmp_path):
    tesseract.text = {2: "   \n  "}

    assert sorted(controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10)) == [4]


def test_nothing_happens_when_disabled(
    controller, flagged, tesseract, tmp_path, monkeypatch
):
    monkeypatch.setattr(controller.settings, "OCR_UNREADABLE_PAGES", False)

    assert controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10) == {}
    assert tesseract.calls == []


def test_a_missing_tesseract_keeps_the_ingest_going(
    controller, flagged, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr("application.arabic_extraction.registry.ALL_EXTRACTORS", ())

    with caplog.at_level("WARNING"):
        assert controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10) == {}

    assert any("cannot run" in record.message for record in caplog.records)


def test_a_failing_qalam_keeps_the_ingest_going(
    controller, tesseract, tmp_path, monkeypatch, caplog
):
    from application.arabic_extraction.extractors.qalam_extractor import QalamExtractor

    def explode(cls, path, start, end):
        raise RuntimeError("qalam could not parse this PDF")

    monkeypatch.setattr(
        QalamExtractor, "available", classmethod(lambda cls: (True, ""))
    )
    monkeypatch.setattr(QalamExtractor, "pages_needing_ocr", classmethod(explode))

    with caplog.at_level("WARNING"):
        assert controller.ocr_unreadable_pages(tmp_path / "x.pdf", 0, 10) == {}

    assert tesseract.calls == []
    assert any("could not check" in record.message for record in caplog.records)


# --- telling text from a picture -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        OCR_ARABIC,
        # A slide whose only text is its title: short, and still a real page.
        "Why statistics?",
        # A table of contents: half its tokens are dot leaders and page numbers.
        "Contents 1.1 Mathematical Background . . . . .......... 4 "
        "1.1.2 The Cauchy-Schwarz inequality . ........... 4 1.1.4 The mean value theorem. ....... 7",
    ],
    ids=["arabic prose", "slide title", "table of contents"],
)
def test_real_text_is_accepted(text):
    assert ProcessService._reads_as_text(text)


@pytest.mark.parametrize(
    "text",
    [
        '©. L ©. ©. U ©. [1] [ J Cc 0" a REE pn. SIAC Ph 7 بي ل Sho 1 PUI EQN Zo',
        "",
        "12 . 14 ....",
        "Zo",
    ],
    ids=["cover photograph", "empty", "numbers only", "one fragment"],
)
def test_noise_is_rejected(text):
    assert not ProcessService._reads_as_text(text)


# --- qalam, for real -----------------------------------------------------------


def test_qalam_flags_an_image_only_page_within_the_slice(tmp_path):
    """Against the real library: a page with a text layer passes, a page that is
    only a picture is flagged, and the index is the document's, not the slice's."""
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("qalam")

    from application.arabic_extraction.extractors.qalam_extractor import QalamExtractor

    pdf = tmp_path / "mixed.pdf"

    with pymupdf.open() as document:
        for index in range(4):
            page = document.new_page()

            if index == 2:
                # A picture and nothing else: no glyphs are painted.
                pixmap = pymupdf.Pixmap(
                    pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False
                )
                pixmap.clear_with(200)
                page.insert_image(page.rect, pixmap=pixmap)
            else:
                page.insert_text(
                    (72, 72), f"Page {index} has an ordinary text layer on it."
                )

        document.save(pdf)

    assert QalamExtractor.pages_needing_ocr(pdf, 1, 4) == [2]


# --- the parse stage -----------------------------------------------------------


def test_an_ocred_page_replaces_its_text_and_drops_its_boxes(monkeypatch):
    """The boxes measured the old text -- nothing, or glyph garbage -- and OCR
    has no positions, so a highlight from them would point at the wrong words."""
    from application.tasks.jobs.ingest import parse

    monkeypatch.setattr(
        ProcessService,
        "ocr_unreadable_pages",
        lambda self, path, start, end: {1: OCR_ARABIC},
    )

    untouched = {
        "page_index": 0,
        "text": "kept",
        "starts": [0],
        "words": ["kept"],
        "boxes": [[0, 0, 1, 1, 0, 0, 0]],
    }
    scanned = {
        "page_index": 1,
        "text": "",
        "starts": [0],
        "words": ["x"],
        "boxes": [[0, 0, 1, 1, 0, 0, 0]],
    }

    parse._fill_unreadable_pages(Path("x.pdf"), 0, 2, [untouched, scanned])

    assert scanned["text"] == OCR_ARABIC
    assert scanned["starts"] == scanned["words"] == scanned["boxes"] == []
    assert untouched["text"] == "kept" and untouched["boxes"], (
        "a readable page was touched"
    )


# --- the highlight a reader sees -----------------------------------------------


def _page(boxes, width=595.0, height=842.0):
    from application.services.ingest.PdfLayoutService import PageWords

    return PageWords(
        page_index=0,
        page_label="1",
        width=width,
        height=height,
        text="x" * 30,
        starts=[0] if boxes else [],
        words=["x" * 30] if boxes else [],
        boxes=boxes,
    )


def test_an_ocred_page_is_highlighted_whole():
    """OCR says what the page reads but not where, so a citation marks the
    page rather than nothing -- and says it is approximate."""
    from application.services.ingest.PdfLayoutService import highlight_metadata

    highlight = highlight_metadata(_page(boxes=[]), 5, 20)

    assert highlight["r"] == [[0.0, 0.0, 595.0, 842.0]]
    assert highlight["approx"] == 1
    assert (highlight["w"], highlight["h"]) == (595.0, 842.0)


def test_a_page_with_no_geometry_gets_no_highlight():
    """A text file, or a PDF on another loader: zero size, nothing to mark."""
    from application.services.ingest.PdfLayoutService import highlight_metadata

    assert highlight_metadata(_page(boxes=[], width=0.0, height=0.0), 5, 20) is None


def test_a_page_with_boxes_is_still_highlighted_by_word():
    from application.services.ingest.PdfLayoutService import highlight_metadata

    highlight = highlight_metadata(
        _page(boxes=[(10.0, 20.0, 110.0, 30.0, 0, 0, 0)]), 0, 30
    )

    assert highlight["r"] == [[10.0, 20.0, 110.0, 30.0]]
    assert "approx" not in highlight


def test_a_chunk_of_an_ocred_page_carries_the_whole_page_highlight(monkeypatch):
    """Through the queue hop: the page dict parse stores, rebuilt and chunked
    the way assemble does it."""
    from application.services.ingest.PdfLayoutService import page_to_dict
    from application.tasks.jobs.ingest import parse
    from application.tasks.jobs.ingest.assemble import _documents

    monkeypatch.setattr(
        ProcessService,
        "ocr_unreadable_pages",
        lambda self, path, start, end: {0: OCR_ARABIC},
    )

    page = page_to_dict(_page(boxes=[]))
    page["text"] = ""
    parse._fill_unreadable_pages(Path("x.pdf"), 0, 1, [page])

    documents, layout, scales, _ = _documents("book.pdf", [page])
    controller = ProcessService()
    controller._pdf_pages = layout
    controller._text_scale = scales

    chunks = controller.split_file(documents, extension=".pdf")

    assert chunks
    assert all(
        chunk.metadata["highlight"]["r"] == [[0.0, 0.0, 595.0, 842.0]]
        for chunk in chunks
    )
