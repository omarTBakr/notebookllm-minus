"""Citations resolve the source name at read time.

The name is copied into the vector payload at index time, so a rename would
otherwise leave every citation showing the old name forever.
"""

from controllers import ChatController


def hit(asset_id="a1", source="old-name.txt", score=0.5, order=3):
    return {"score": score, "metadata": {"asset_id": asset_id, "source": source,
                                         "chunk_order": order}}


def test_live_name_wins_over_the_indexed_copy():
    [cite] = ChatController.to_citations([hit()], {"a1": "new-name.txt"})

    assert cite["source"] == "new-name.txt"


def test_falls_back_to_the_indexed_copy_for_a_deleted_asset():
    """A stale name beats "unknown" when the asset is gone."""
    [cite] = ChatController.to_citations([hit()], {})

    assert cite["source"] == "old-name.txt"


def test_falls_back_to_unknown_when_nothing_is_known():
    [cite] = ChatController.to_citations([{"score": 0.1, "metadata": {}}], {})

    assert cite["source"] == "unknown"


def test_numbers_are_one_based_and_sequential():
    cites = ChatController.to_citations([hit(), hit(), hit()], {})

    assert [c["num"] for c in cites] == [1, 2, 3]


def test_carries_asset_id_and_chunk_order_through():
    [cite] = ChatController.to_citations([hit()], {})

    assert cite["asset_id"] == "a1"
    assert cite["chunk_order"] == 3


def test_score_is_rounded():
    [cite] = ChatController.to_citations([hit(score=0.123456)], {})

    assert cite["score"] == 0.1235


def test_names_may_be_omitted_entirely():
    [cite] = ChatController.to_citations([hit()])

    assert cite["source"] == "old-name.txt"


def test_a_zero_score_is_reported_as_zero_not_dropped():
    """Regression: the guard was `if hit.get("score")`, so a genuine 0.0 —
    a real, perfectly-orthogonal match — was reported as None."""
    [cite] = ChatController.to_citations([hit(score=0.0)], {})

    assert cite["score"] == 0.0


# --- the page a citation points at --------------------------------------------
#
# `pages` maps (asset_id, chunk_order) to the fields routes/chat/_pages.py
# resolved from the chunk row. Three different things are called a page number
# in this codebase and only one of them is safe to show, so these pin down
# which is which. See routes/chat/_pages.py.


def test_page_number_is_one_based():
    """chunk_metadata["page"] is 0-based; a citation's page_number is not."""
    pages = {("a1", 3): {"page_number": 11, "page_label": "11"}}

    [cite] = ChatController.to_citations([hit()], {}, pages)

    assert cite["page_number"] == 11


def test_a_roman_label_is_kept_as_written_beside_a_physical_page():
    """Front matter is the case that punishes parsing the label.

    A page labelled "iii" is physically page 3. The viewer needs the 3; the
    reader needs the "iii". Anything that derives one from the other by
    parsing opens the book at the wrong place.
    """
    pages = {("a1", 3): {"page_number": 3, "page_label": "iii"}}

    [cite] = ChatController.to_citations([hit()], {}, pages)

    assert cite["page_label"] == "iii"
    assert cite["page_number"] == 3


def test_a_chunk_with_no_page_carries_none():
    """A .txt note has no pages, and that is not an error."""
    [cite] = ChatController.to_citations([hit()], {}, {})

    assert cite["page_number"] is None
    assert cite["page_label"] is None


def test_pages_may_be_omitted_entirely():
    """The argument is optional, mirroring `names`."""
    [cite] = ChatController.to_citations([hit()])

    assert cite["page_number"] is None


def test_chunk_order_zero_resolves():
    """0 is the first chunk of every document, and it is falsy.

    A lookup keyed on a truthiness check would drop the page for exactly the
    passage most likely to be cited — the opening of a document.
    """
    pages = {("a1", 0): {"page_number": 1, "page_label": "1"}}

    [cite] = ChatController.to_citations([hit(order=0)], {}, pages)

    assert cite["page_number"] == 1
