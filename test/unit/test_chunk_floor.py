"""Debris chunks, and why they are merged rather than emitted.

The recursive splitter flushes the short splits it has accumulated as soon as
the next split needs recursion. A page that opens with a running header — which
is most pages of a real book — therefore produces the header as a chunk of its
own before the body is ever split. Those chunks are embedded, and there are
enough of them that one lands in the top 5 of an unrelated query: the observed
symptom was a grounded answer citing five single Arabic words and correctly
reporting that they meant nothing.
"""

import pytest
from langchain_core.documents import Document

from controllers.TextProcessingController import TextProcessingController

BODY = "اليسار حينئذ بديدو ومعناه الهاربة وحدث في أيام بيكماليون أن رامان نيرار " * 20
HEADER = "سورية"


def _controller(floor=100, chunk_size=1000, overlap=200):
    controller = TextProcessingController(chunk_size=chunk_size, chunk_overlap=overlap)
    controller.settings.MIN_CHUNK_CHARS = floor
    return controller


def _page(text, page=0, source="b.pdf"):
    return Document(page_content=text, metadata={"source": source, "page": page})


def test_the_defect_reproduces_without_the_floor():
    """Pin the upstream behaviour this exists to correct, so that a langchain
    change which fixes it upstream shows up here as a failure rather than as
    silently dead code."""
    controller = _controller(floor=0)

    chunks = controller.split([_page(f"{HEADER}\n\n{BODY}")])

    assert any(c.page_content == HEADER for c in chunks), (
        "expected the splitter to orphan the header into its own chunk"
    )


def test_a_running_header_is_folded_into_the_body():
    controller = _controller()

    chunks = controller.split([_page(f"{HEADER}\n\n{BODY}")])

    assert all(len(c.page_content) >= 100 for c in chunks)
    assert chunks[0].page_content.startswith(HEADER), "the header text was dropped, not merged"


def test_a_trailing_page_number_is_folded_backwards():
    controller = _controller()

    chunks = controller.split([_page(f"{BODY}\n\n٤٧")])

    assert all(len(c.page_content) >= 100 for c in chunks)
    assert chunks[-1].page_content.endswith("٤٧")


def test_chunks_are_never_merged_across_pages():
    """`start_index` is an offset into one page's text, and a citation
    highlight built from a chunk spanning two pages would point at the wrong
    page. A short chunk with no same-page neighbour stays as it is."""
    controller = _controller()

    chunks = controller.split([_page(HEADER, page=0), _page(BODY, page=1)])

    pages = [c.metadata["page"] for c in chunks]
    assert 0 in pages, "the short page was dropped instead of kept"
    header_chunks = [c for c in chunks if c.metadata["page"] == 0]
    assert len(header_chunks) == 1
    assert header_chunks[0].page_content == HEADER


def test_a_genuinely_short_page_survives():
    """A title page is short because the page is short, not because it was cut
    badly. Losing it would lose the document's title from the index."""
    controller = _controller()

    chunks = controller.split([_page("ذخائر لبنان — تأليف إبراهيم الأسود")])

    assert len(chunks) == 1
    assert "ذخائر" in chunks[0].page_content


def test_merging_respects_the_size_ceiling():
    """The floor must not undo the ceiling. A chunk may grow by at most one
    floor's worth — enough to absorb anything this method is willing to move —
    and a merge that would exceed that is declined, leaving the short chunk
    where it is. Called directly, so the inputs are shaped by hand rather than
    by the splitter."""
    controller = _controller(floor=100, chunk_size=200, overlap=0)

    page = "ا" * 290 + "\n\n" + "ب" * 50
    chunks = controller.merge_undersized(
        [
            Document(page_content="ا" * 290, metadata={"source": "b.pdf", "page": 0, "start_index": 0}),
            Document(page_content="ب" * 50, metadata={"source": "b.pdf", "page": 0, "start_index": 292}),
        ],
        [_page(page)],
    )

    assert len(chunks) == 2, "merged past the ceiling"


def test_a_merge_within_the_ceiling_is_allowed():
    """The other side of the same rule: chunk_size + floor is the budget, and a
    merge that fits inside it goes ahead."""
    controller = _controller(floor=100, chunk_size=200, overlap=0)

    page = "ا" * 200 + "\n\n" + "ب" * 10
    chunks = controller.merge_undersized(
        [
            Document(page_content="ا" * 200, metadata={"source": "b.pdf", "page": 0, "start_index": 0}),
            Document(page_content="ب" * 10, metadata={"source": "b.pdf", "page": 0, "start_index": 202}),
        ],
        [_page(page)],
    )

    assert len(chunks) == 1
    assert len(chunks[0].page_content) <= 200 + 100


def test_start_index_follows_the_absorbed_heading():
    """When a heading is folded forward into the body, the pair starts where
    the heading did — otherwise the highlight omits the line it just took on."""
    controller = _controller()

    page = f"{HEADER}\n\n{BODY[:900]}"
    chunks = controller.merge_undersized(
        [
            Document(page_content=HEADER, metadata={"source": "b.pdf", "page": 0, "start_index": 0}),
            Document(page_content=BODY[:900], metadata={"source": "b.pdf", "page": 0, "start_index": 7}),
        ],
        [_page(page)],
    )

    assert len(chunks) == 1
    assert chunks[0].metadata["start_index"] == 0


def test_the_floor_can_be_switched_off():
    controller = _controller(floor=0)

    chunks = controller.merge_undersized(
        [
            Document(page_content="x", metadata={"source": "b.pdf", "page": 0}),
            Document(page_content="y", metadata={"source": "b.pdf", "page": 0}),
        ],
        [_page("x\n\ny")],
    )

    assert len(chunks) == 2


def test_ordinary_prose_is_untouched():
    """On a document with no debris this is a no-op — same count, same text."""
    controller = _controller()
    pages = [_page(BODY, page=n) for n in range(3)]

    with_floor = controller.split(pages)
    controller.settings.MIN_CHUNK_CHARS = 0
    without = controller.split(pages)

    assert [c.page_content for c in with_floor] == [c.page_content for c in without]
