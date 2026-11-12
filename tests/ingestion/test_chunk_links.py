"""Figure <-> passage links: by label, by position on the page, by page alone."""

from types import SimpleNamespace

from application.services.ingest.ChunkLinker import (
    LinkRules,
    labels_in,
    link_chunks,
    score_pair,
)


def text(order, content, page=0, rects=None, approx=False):
    highlight = None
    if rects is not None:
        highlight = {"v": 1, "w": 600, "h": 800, "o": "tl", "r": rects}
        if approx:
            highlight["approx"] = 1
    return SimpleNamespace(
        chunk_order=order,
        chunk_content=content,
        chunk_metadata={"page": page, "kind": "text", **({"highlight": highlight} if highlight else {})},
    )


def image(order, caption, page=0, bbox=(100, 200, 500, 500), nearby="", whole_page=False):
    meta = {"page": page, "kind": "image", "bbox": list(bbox)}
    if nearby:
        meta["nearby_text"] = nearby
    if whole_page:
        meta["whole_page"] = True
    return SimpleNamespace(chunk_order=order, chunk_content=caption, chunk_metadata=meta)


# --- labels -----------------------------------------------------------------------------


def test_labels_in_reads_english_and_arabic_with_arabic_indic_digits():
    assert labels_in("see Fig. 3 and Table 2") == {("figure", "3"), ("table", "2")}
    assert labels_in("كما يبيّن الشكل ٣") == {("figure", "3")}
    assert labels_in("انظر جدول ٢") == {("table", "2")}
    assert labels_in("Figure 3a shows") == {("figure", "3a")}
    assert labels_in("the figure of speech") == set()


# --- pairs ------------------------------------------------------------------------------------


def test_a_reference_links_across_pages():
    figure = image(10, "A bar chart", page=0, nearby="Figure 3: Revenue by quarter")
    passage = text(4, "As Figure 3 shows, Q4 was strongest.", page=7)

    assert score_pair(passage.chunk_metadata, passage.chunk_content, figure.chunk_metadata, figure.chunk_content) >= 10


def test_the_figures_own_label_comes_from_nearby_text_before_the_caption():
    figure = image(10, "Chart mentioning Table 9 in its legend", nearby="Figure 1: Sales")
    yes = text(1, "see Figure 1", page=5)
    no = text(2, "see Table 9", page=5)

    assert score_pair(yes.chunk_metadata, yes.chunk_content, figure.chunk_metadata, figure.chunk_content) > 0
    assert score_pair(no.chunk_metadata, no.chunk_content, figure.chunk_metadata, figure.chunk_content) == 0


def test_the_passage_right_below_the_figure_beats_a_far_one():
    figure = image(10, "chart", bbox=(100, 200, 500, 500))
    below = text(1, "caption-ish", rects=[[100, 510, 500, 540]])
    far = text(2, "unrelated", rects=[[100, 740, 500, 780]])

    close = score_pair(below.chunk_metadata, "x", figure.chunk_metadata, "chart")
    distant = score_pair(far.chunk_metadata, "x", figure.chunk_metadata, "chart")
    assert close > distant > 0


def test_the_other_column_only_shares_the_page():
    figure = image(10, "chart", bbox=(50, 200, 280, 500))
    other_column = text(1, "x", rects=[[320, 210, 560, 480]])

    assert score_pair(other_column.chunk_metadata, "x", figure.chunk_metadata, "chart") == 0.5


def test_an_approximate_highlight_or_no_geometry_links_weakly_by_page():
    figure = image(10, "chart")
    approx = text(1, "x", rects=[[0, 0, 600, 800]], approx=True)
    bare = text(2, "x")
    elsewhere = text(3, "x", page=3)

    assert score_pair(approx.chunk_metadata, "x", figure.chunk_metadata, "chart") == 0.5
    assert score_pair(bare.chunk_metadata, "x", figure.chunk_metadata, "chart") == 0.5
    assert score_pair(elsewhere.chunk_metadata, "x", figure.chunk_metadata, "chart") == 0


# --- the whole asset ---------------------------------------------------------------------------


def test_link_chunks_writes_both_sides_best_first_and_capped():
    chunks = [
        text(0, "Intro text.", page=0, rects=[[100, 60, 500, 150]]),
        text(1, "Figure 1: Revenue by quarter, in millions.", page=0, rects=[[100, 510, 500, 540]]),
        text(2, "Unrelated paragraph on the same page.", page=0, rects=[[100, 600, 500, 700]]),
        text(3, "As Figure 1 shows, Q4 was the strongest quarter.", page=4, rects=[[100, 60, 500, 100]]),
        image(4, "A bar chart of four quarters", page=0, nearby="Figure 1: Revenue by quarter"),
    ]

    links = link_chunks(chunks, LinkRules(max_text_per_image=2, max_images_per_text=2))

    assert chunks[4].chunk_metadata["linked_chunks"] == [1, 3]  # the label wins over position
    assert chunks[1].chunk_metadata["linked_images"] == [4]
    assert chunks[3].chunk_metadata["linked_images"] == [4]
    assert chunks[2].chunk_metadata["linked_images"] == [4]  # same page, weak, still recorded
    assert "linked_images" not in chunks[0].chunk_metadata or chunks[0].chunk_metadata["linked_images"] == [4]
    assert links >= 5


def test_an_asset_without_pictures_or_without_text_is_left_alone():
    only_text = [text(0, "a"), text(1, "b")]
    only_images = [image(0, "chart")]

    assert link_chunks(only_text) == 0
    assert link_chunks(only_images) == 0
    assert "linked_images" not in only_text[0].chunk_metadata
