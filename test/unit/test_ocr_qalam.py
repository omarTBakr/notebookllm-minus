"""`QalamExtractor` — the one extractor here that wraps somebody else's
answer to this package's own problem, rather than being it.

The real `qalam` package is a Rust extension published on PyPI and is not
installed in this environment (`import qalam` fails), which is also true of
most CI boxes that never `pip install qalam`. None of that should stop this
file from running: every test here installs a fake `qalam` module into
`sys.modules` rather than the real package, so what is exercised is
`QalamExtractor`'s own logic — the path-keyed cache, the 0-based-to-1-based
page conversion, and what `inspect` hands back — not qalam itself.
"""

from __future__ import annotations

import sys
import types

import pytest

from arabic_extraction.base import Page
from arabic_extraction.extractors.qalam_extractor import QalamExtractor


class FakeDocument:
    """Stands in for `qalam.Document`: pages addressed 1-based, as qalam
    counts them, plus the confidence/needs-ocr verdict `inspect` surfaces."""

    def __init__(self, path):
        self.path = path
        self.page_calls: list[int] = []
        self.confidence = 0.97
        self.pages_needing_ocr = [3]

    def page(self, number):
        self.page_calls.append(number)
        return types.SimpleNamespace(text=f"page-{number}")

    def __len__(self):
        return 5


@pytest.fixture(autouse=True)
def _clear_document_cache():
    """`_documents` is a class attribute, shared by every instance — and,
    without this, by every test in the process. Left alone, whichever test
    runs first would cache a `FakeDocument` under a path another test reuses
    (tmp_path names collide across tests only rarely, but the cache must not
    depend on that)."""
    QalamExtractor._documents.clear()
    yield
    QalamExtractor._documents.clear()


@pytest.fixture
def fake_qalam(monkeypatch):
    """Install a fake `qalam` module so `_extract`/`inspect` can be exercised
    without the real Rust package. Returns the list of keys `Document(...)`
    was constructed with, in order — the thing the caching tests check."""
    calls: list[str] = []

    def _document(path):
        calls.append(path)
        return FakeDocument(path)

    monkeypatch.setitem(sys.modules, "qalam", types.SimpleNamespace(Document=_document))
    return calls


# --- availability ----------------------------------------------------------


def test_available_is_false_when_qalam_is_not_installed(monkeypatch):
    """`None` in `sys.modules` is the standard way to force an `ImportError`
    deterministically, regardless of whether the real wheel happens to be on
    this machine — the survey (`test_ocr_extractors.py`) depends on this
    reporting a reason rather than raising."""
    monkeypatch.setitem(sys.modules, "qalam", None)

    ok, reason = QalamExtractor.available()

    assert not ok
    assert "qalam" in reason.lower()


def test_available_is_true_when_qalam_can_be_imported(fake_qalam):
    ok, reason = QalamExtractor.available()

    assert ok
    assert reason == ""


# --- the per-path cache ------------------------------------------------------


def test_the_document_is_parsed_once_per_path(fake_qalam, tmp_path):
    """`qalam.Document(path)` parses the whole file eagerly on construction,
    and the harness asks for one page at a time, usually every page of the
    same file before moving to the next one. Reparsing on every page would
    multiply the cost by the page count for no reason — see the class's own
    docstring."""
    path = tmp_path / "book.pdf"

    QalamExtractor()._extract(Page(path=path, number=0))
    QalamExtractor()._extract(Page(path=path, number=1))

    assert fake_qalam == [str(path)]


def test_two_paths_get_independent_cache_entries(fake_qalam, tmp_path):
    """The cache is keyed by path, not shared globally — a second document
    must not reuse, or evict, the first one's entry."""
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"

    QalamExtractor()._extract(Page(path=first, number=0))
    QalamExtractor()._extract(Page(path=second, number=0))

    assert fake_qalam == [str(first), str(second)]
    assert QalamExtractor._documents[str(first)] is not QalamExtractor._documents[str(second)]


# --- page numbering ----------------------------------------------------------


def test_the_harnesss_0_based_page_becomes_qalams_1_based_page(fake_qalam, tmp_path):
    """qalam counts pages from 1; the harness counts from 0, as pymupdf does.
    Getting this backwards silently misfiles every page by one — the same
    class of bug flagged for LLM-provided page numbers elsewhere in this
    package."""
    path = tmp_path / "book.pdf"

    text = QalamExtractor()._extract(Page(path=path, number=0))

    assert text == "page-1"
    assert QalamExtractor._documents[str(path)].page_calls == [1]

    text = QalamExtractor()._extract(Page(path=path, number=4))

    assert text == "page-5"


# --- inspect() -----------------------------------------------------------------


def test_inspect_surfaces_qalams_confidence_and_ocr_verdict(fake_qalam, tmp_path):
    """Not part of the `ArabicExtractor` contract — the harness only ever asks
    for text — but qalam is the one extractor here that says which pages it
    does not trust, which is worth surfacing outside the benchmark loop."""
    path = tmp_path / "book.pdf"

    result = QalamExtractor.inspect(path)

    assert result == {"confidence": 0.97, "pages_needing_ocr": [3], "pages": 5}


def test_inspect_reuses_the_same_cached_document_as_extract(fake_qalam, tmp_path):
    """`inspect` is meant for outside the benchmark loop, not a second parse
    of the same file — it goes through `_document` exactly like `_extract`."""
    path = tmp_path / "book.pdf"

    QalamExtractor()._extract(Page(path=path, number=0))
    QalamExtractor.inspect(path)

    assert fake_qalam == [str(path)]


# --- failure propagation -------------------------------------------------------


def test_a_failure_inside_extract_is_not_swallowed_here(monkeypatch, tmp_path):
    """`_extract` must let the exception through unchanged. Turning it into a
    recorded, non-raising result is `ArabicExtractor.run`'s job, already
    covered generically in test_ocr_extractors.py — `_extract` must not
    pre-empt or duplicate that."""

    def _exploding_document(path):
        raise RuntimeError("corrupt PDF, qalam cannot parse it")

    monkeypatch.setitem(sys.modules, "qalam", types.SimpleNamespace(Document=_exploding_document))

    with pytest.raises(RuntimeError, match="corrupt PDF"):
        QalamExtractor()._extract(Page(path=tmp_path / "book.pdf", number=0))
