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
