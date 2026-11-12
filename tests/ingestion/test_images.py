"""Pictures in documents: finding figures, captioning them, cutting them out.

Every PDF here is built with pymupdf in the test, so the cases (a real figure,
a logo on every page, a tiny icon, a picture-only page) are explicit rather
than hidden inside a fixture file.
"""

import io

import pymupdf
import pytest
from PIL import Image

from application.services.ingest.ImageCaptionService import (
    ImageCaptionService,
    fallback_caption,
    nearby_text,
)
from application.services.ingest.ImageRegionService import (
    FigureRules,
    find_figures,
    page_has_marks,
    render_region,
    whole_page_region,
)
from application.tasks.jobs.ingest.assemble import image_chunk_metadata
from shared.utils.images import InvalidImageError, prepare, sniff_mime


def png(width, height, colour=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def figures_pdf(tmp_path):
    """Five pages: a logo on every page, a figure and an icon on page 1, a
    picture-only page 3, and a blank page 4."""
    doc = pymupdf.open()
    logo = png(200, 200, (10, 10, 10))
    for index in range(5):
        page = doc.new_page(width=600, height=800)
        if index != 4:
            # Same bytes on four pages: pymupdf stores it once, one xref.
            page.insert_image(pymupdf.Rect(20, 20, 120, 120), stream=logo)
        if index == 1:
            page.insert_image(pymupdf.Rect(100, 200, 500, 500), stream=png(800, 600))
            page.insert_text((100, 530), "Figure 3: Revenue by quarter", fontsize=11)
            page.insert_text((100, 545), "in millions of dollars", fontsize=11)
            page.insert_image(pymupdf.Rect(300, 700, 320, 720), stream=png(40, 40, (0, 200, 0)))
        if index == 3:
            page.insert_image(pymupdf.Rect(0, 0, 600, 800), stream=png(600, 800, (0, 0, 200)))
    path = tmp_path / "figures.pdf"
    doc.save(path)
    return path


# --- finding figures --------------------------------------------------------------


def test_finds_the_figure_and_drops_logo_and_icon(figures_pdf):
    found = find_figures(figures_pdf, 0, 5, FigureRules(min_side_px=96, min_area_ratio=0.02, max_repeat=3))

    assert list(found) == [1, 3]
    (figure,) = found[1]
    assert figure["bbox"] == [100.0, 200.0, 500.0, 500.0]
    assert figure["xref"]


def test_a_logo_under_the_repeat_limit_is_kept(figures_pdf):
    found = find_figures(figures_pdf, 0, 5, FigureRules(min_side_px=96, min_area_ratio=0.0, max_repeat=10))

    assert any(region["bbox"] == [20.0, 20.0, 120.0, 120.0] for region in found[0])


def test_batch_range_is_respected(figures_pdf):
    assert list(find_figures(figures_pdf, 2, 4)) == [3]


def test_page_has_marks_tells_a_picture_page_from_a_blank_one(figures_pdf):
    assert page_has_marks(figures_pdf, 3)
    assert not page_has_marks(figures_pdf, 4)


# --- captions ------------------------------------------------------------------------


def lines_page(lines, label="2", index=1):
    """A parsed page whose words are laid out one line per entry: (y, text)."""
    words, boxes = [], []
    for line_no, (y, text) in enumerate(lines):
        x = 100.0
        for word_no, word in enumerate(text.split()):
            words.append(word)
            boxes.append((x, y, x + 40, y + 10, 0, line_no, word_no))
            x += 45
    return {"page_index": index, "page_label": label, "width": 600, "height": 800, "words": words, "boxes": boxes}


def test_nearby_text_prefers_a_caption_line_with_its_continuation():
    page = lines_page([(150, "Some body text above"), (520, "Figure 3: Revenue"), (535, "by quarter")])

    assert nearby_text(page, [100, 200, 500, 500]) == "Figure 3: Revenue by quarter"


def test_nearby_text_recognises_an_arabic_caption():
    page = lines_page([(510, "نص عادي"), (190, "الشكل ٢ : الإيرادات")])

    assert nearby_text(page, [100, 200, 500, 500]).startswith("الشكل")


def test_nearby_text_ignores_far_lines_and_labels_inside_the_figure():
    page = lines_page([(300, "axis label"), (700, "far away paragraph")])

    assert nearby_text(page, [100, 200, 500, 500]) == ""


def test_fallback_caption_names_the_page_when_nothing_is_near():
    page = lines_page([], label="iv")

    assert fallback_caption(page, {"bbox": [0, 0, 10, 10]}) == "[image, page iv]"


class _Captioner:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    async def generate_text(self, prompt, images=None, **kwargs):
        self.calls.append((prompt, images))
        if self.fail:
            raise RuntimeError("model down")
        return self.reply


async def test_caption_service_writes_model_captions_with_the_picture_attached():
    page = lines_page([(520, "Figure 3: Revenue")])
    page["images"] = [{"bbox": [100, 200, 500, 500], "xref": 7}]
    client = _Captioner(reply="  A bar chart\nof revenue.  ")

    written = await ImageCaptionService(client, render=lambda p, r: (b"png", "image/png")).caption([page])

    assert written == 1
    assert page["images"][0]["caption"] == "A bar chart of revenue."
    prompt, images = client.calls[0]
    assert images == [(b"png", "image/png")]
    assert "Figure 3: Revenue" in prompt  # the nearby text is given as context


async def test_a_failing_model_falls_back_instead_of_failing_the_batch():
    page = lines_page([(520, "Figure 3: Revenue")])
    page["images"] = [{"bbox": [100, 200, 500, 500]}]

    written = await ImageCaptionService(_Captioner(fail=True), render=lambda p, r: (b"x", "image/png")).caption([page])

    assert written == 0
    assert page["images"][0]["caption"] == "Figure 3: Revenue"


# --- chunks ------------------------------------------------------------------------------


def test_image_chunk_metadata_mirrors_a_text_chunks_shape():
    pages = [
        {"page_index": 0, "page_label": "1", "width": 600, "height": 800, "text": "x"},
        {
            "page_index": 1,
            "page_label": "2",
            "width": 600,
            "height": 800,
            "text": "",
            "images": [{"bbox": [1, 2, 3, 4], "xref": 9, "caption": "A chart"}, whole_page_region(600, 800)],
        },
    ]

    first, second = image_chunk_metadata("doc.pdf", pages)

    caption, meta = first
    assert caption == "A chart"
    assert meta["kind"] == "image" and meta["page"] == 1 and meta["page_label"] == "2"
    assert meta["highlight"] == {"v": 1, "w": 600, "h": 800, "o": "tl", "r": [[1, 2, 3, 4]]}
    assert meta["xref"] == 9
    assert second[0] == "[image, page 2]"  # no caption recorded
    assert second[1]["whole_page"] is True


# --- pixels ---------------------------------------------------------------------------------


def test_render_region_cuts_the_figure_out_of_the_pdf(figures_pdf):
    data, mime = render_region(
        figures_pdf.read_bytes(),
        is_pdf=True,
        content_hash="h1",
        page_index=1,
        bbox=[100, 200, 500, 500],
        max_side=256,
    )

    image = Image.open(io.BytesIO(data))
    assert mime == "image/png"
    assert max(image.size) == 256
    # The figure is solid red; a crop of the right box is red in the middle.
    r, g, b = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
    assert r > 150 and g < 100


def test_render_region_returns_an_uploaded_picture_itself():
    data, mime = render_region(png(2000, 1000), is_pdf=False, content_hash="h2", page_index=0, bbox=None, max_side=500)

    assert Image.open(io.BytesIO(data)).size == (500, 250)


def test_prepare_rejects_what_is_not_an_image():
    with pytest.raises(InvalidImageError):
        prepare(b"%PDF-1.7 not a picture")


def test_sniff_mime_reads_the_bytes_not_the_name():
    assert sniff_mime(png(4, 4)) == "image/png"
    assert sniff_mime(b"hello") is None
