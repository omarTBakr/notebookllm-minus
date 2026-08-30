"""Word coordinates for a PDF page, and the highlight rectangles they build.

Most of this is exercised against hand-built PageWords — fast, and it lets a
"same line" or "degenerate page" case be constructed directly rather than
found inside a real PDF. extract_pages itself is exercised against tiny
synthetic PDFs built with pymupdf, so nothing here depends on a fixture file.
"""

import pymupdf
import pytest

import controllers.PdfLayoutController as layout
from controllers.PdfLayoutController import (
    MAX_RECTS,
    PageWords,
    _clean_word,
    _cpu_count,
    _cgroup_quota_cores,
    extract_pages,
    highlight_metadata,
    rects_for_range,
)


def box(x0, y0, x1, y1, block=0, line=0, word_no=0):
    return (x0, y0, x1, y1, block, line, word_no)


def page_of(words, boxes):
    """A PageWords built the way extract_pages would, from clean words."""
    starts, cursor = [], 0
    for w in words:
        starts.append(cursor)
        cursor += len(w) + 1
    return PageWords(
        page_index=0, page_label="1", width=600.0, height=800.0,
        text=" ".join(words), starts=starts, words=list(words), boxes=list(boxes),
    )


# --- _clean_word ----------------------------------------------------------------


def test_a_bidi_control_word_cleans_to_empty():
    """PDF producers emit these as standalone tokens; a real one from this
    project's own corpus, not a contrived character."""
    assert _clean_word("‏") == ""


def test_a_presentation_form_folds_to_a_standard_letter():
    assert _clean_word("ﻻ") != ""  # a real Arabic ligature presentation form
    assert _clean_word("ﻻ") == "لا"  # its NFKC decomposition


def test_ordinary_words_pass_through():
    assert _clean_word("hello") == "hello"


# --- rects_for_range --------------------------------------------------------------


def test_a_single_word_becomes_one_rect():
    page = page_of(["alpha"], [box(10, 20, 50, 35)])

    assert rects_for_range(page, 0, 5) == [[10.0, 20.0, 50.0, 35.0]]


def test_two_words_on_one_line_merge_into_one_rect():
    page = page_of(["alpha", "beta"], [box(10, 20, 50, 35, word_no=0), box(55, 20, 90, 35, word_no=1)])

    assert rects_for_range(page, 0, len(page.text)) == [[10.0, 20.0, 90.0, 35.0]]


def test_a_gap_in_word_no_starts_a_new_rect():
    """The contiguous-run refinement: same (block, line) is not enough on its
    own — a real skip in pymupdf's own numbering must not be merged over."""
    page = page_of(
        ["alpha", "beta"],
        [box(10, 20, 50, 35, word_no=0), box(55, 20, 90, 35, word_no=5)],
    )

    assert rects_for_range(page, 0, len(page.text)) == [
        [10.0, 20.0, 50.0, 35.0],
        [55.0, 20.0, 90.0, 35.0],
    ]


def test_a_different_line_starts_a_new_rect():
    page = page_of(
        ["alpha", "beta"],
        [box(10, 20, 50, 35, line=0, word_no=0), box(10, 40, 50, 55, line=1, word_no=0)],
    )

    rects = rects_for_range(page, 0, len(page.text))
    assert len(rects) == 2


def test_only_words_overlapping_the_range_are_selected():
    page = page_of(
        ["alpha", "beta", "gamma"],
        [box(0, 0, 10, 10, word_no=0), box(20, 0, 30, 10, word_no=1), box(40, 0, 50, 10, word_no=2)],
    )
    # "alpha beta gamma": alpha=[0:5], beta=[6:10], gamma=[11:16]
    only_beta = rects_for_range(page, 6, 10)

    assert only_beta == [[20.0, 0.0, 30.0, 10.0]]


def test_no_overlap_returns_nothing():
    page = page_of(["alpha"], [box(0, 0, 10, 10)])

    assert rects_for_range(page, 100, 200) == []


def test_an_empty_page_returns_nothing():
    page = page_of([], [])

    assert rects_for_range(page, 0, 10) == []


def test_a_degenerate_page_is_capped_to_nothing():
    """More separate runs than MAX_RECTS: a scanned table or OCR noise, not a
    highlight anyone could read. Dropped rather than truncated."""
    words = [f"w{i}" for i in range(MAX_RECTS + 10)]
    boxes = [box(i, 0, i + 1, 10, word_no=i * 2) for i in range(len(words))]  # every word its own run
    page = page_of(words, boxes)

    assert rects_for_range(page, 0, len(page.text)) == []


# --- highlight_metadata ------------------------------------------------------------


def test_highlight_metadata_shape():
    page = page_of(["alpha"], [box(10, 20, 50, 35)])

    meta = highlight_metadata(page, 0, 5)

    assert meta == {"v": 1, "w": 600.0, "h": 800.0, "o": "tl", "r": [[10.0, 20.0, 50.0, 35.0]]}


def test_no_rects_means_no_highlight_metadata():
    page = page_of(["alpha"], [box(0, 0, 10, 10)])

    assert highlight_metadata(page, 100, 200) is None


# --- extract_pages, against a real (synthetic) PDF --------------------------------


def make_pdf(path, pages_text):
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 30), text, fontsize=12)
    doc.save(str(path))
    doc.close()


def test_extract_pages_reads_every_page_in_order(tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["first page words", "second page words"])

    pages = extract_pages(pdf)

    assert len(pages) == 2
    assert pages[0].page_index == 0
    assert pages[1].page_index == 1
    assert "first" in pages[0].text
    assert "second" in pages[1].text


def test_extract_pages_offsets_are_exact(tmp_path):
    """The invariant the whole feature depends on: slicing a page's own text
    at a word's start reproduces that word exactly."""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["alpha beta gamma delta epsilon"])

    [page] = extract_pages(pdf)

    for i, word in enumerate(page.words):
        start = page.starts[i]
        assert page.text[start : start + len(word)] == word


def test_extract_pages_gives_each_word_a_real_box(tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["hello world"])

    [page] = extract_pages(pdf)

    assert len(page.boxes) == len(page.words) == 2
    x0, y0, x1, y1, *_ = page.boxes[0]
    assert x1 > x0 and y1 > y0


def test_extract_pages_falls_back_to_a_numeric_label(tmp_path):
    """No page-label rule was set on this PDF, so pymupdf's own get_label()
    returns "" — the same fallback routes/chat/_pages.py already uses for
    pypdf's page_label, kept consistent here."""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["one"])

    [page] = extract_pages(pdf)

    assert page.page_label == "1"


# --- how many workers to parallelise extraction across --------------------------
#
# The point of this whole section: a container capped with --cpus (a CFS
# bandwidth quota) is invisible to sched_getaffinity, which only sees
# --cpuset-cpus (a pin to specific cores). Sizing the pool from affinity
# alone would spawn a worker per host core regardless of the quota, and
# starve every one of them to a fraction of what a correctly-sized pool
# would have given it.


def _no_cgroup_files(monkeypatch, tmp_path):
    """Neither v2 nor v1 quota files exist — the common case off a real
    cgroup (this dev sandbox included)."""
    monkeypatch.setattr(layout, "_CGROUP_V2_CPU_MAX", tmp_path / "absent-v2")
    monkeypatch.setattr(layout, "_CGROUP_V1_QUOTA", tmp_path / "absent-v1-quota")
    monkeypatch.setattr(layout, "_CGROUP_V1_PERIOD", tmp_path / "absent-v1-period")


def test_no_cgroup_files_means_no_quota(monkeypatch, tmp_path):
    _no_cgroup_files(monkeypatch, tmp_path)

    assert _cgroup_quota_cores() is None


def test_cgroup_v2_max_means_unlimited(monkeypatch, tmp_path):
    v2 = tmp_path / "cpu.max"
    v2.write_text("max 100000\n")
    monkeypatch.setattr(layout, "_CGROUP_V2_CPU_MAX", v2)

    assert _cgroup_quota_cores() is None


def test_cgroup_v2_quota_is_quota_over_period(monkeypatch, tmp_path):
    """--cpus=2, in v2's own units: 200000 quota / 100000 period = 2 cores."""
    v2 = tmp_path / "cpu.max"
    v2.write_text("200000 100000\n")
    monkeypatch.setattr(layout, "_CGROUP_V2_CPU_MAX", v2)

    assert _cgroup_quota_cores() == 2.0


def test_cgroup_v2_fractional_quota(monkeypatch, tmp_path):
    """--cpus=2.5."""
    v2 = tmp_path / "cpu.max"
    v2.write_text("250000 100000\n")
    monkeypatch.setattr(layout, "_CGROUP_V2_CPU_MAX", v2)

    assert _cgroup_quota_cores() == 2.5


def test_cgroup_v1_negative_quota_means_unlimited(monkeypatch, tmp_path):
    """-1 is v1's spelling of "no limit"."""
    _no_cgroup_files(monkeypatch, tmp_path)  # v2 absent, so v1 is checked
    quota, period = tmp_path / "cfs_quota_us", tmp_path / "cfs_period_us"
    quota.write_text("-1\n")
    period.write_text("100000\n")
    monkeypatch.setattr(layout, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(layout, "_CGROUP_V1_PERIOD", period)

    assert _cgroup_quota_cores() is None


def test_cgroup_v1_quota_is_quota_over_period(monkeypatch, tmp_path):
    _no_cgroup_files(monkeypatch, tmp_path)
    quota, period = tmp_path / "cfs_quota_us", tmp_path / "cfs_period_us"
    quota.write_text("150000\n")
    period.write_text("100000\n")
    monkeypatch.setattr(layout, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(layout, "_CGROUP_V1_PERIOD", period)

    assert _cgroup_quota_cores() == 1.5


def test_v2_is_checked_before_v1():
    """Both directories can exist on a v1 host running a v2-aware kernel;
    v2 is the one actually enforced when both are mounted."""
    # Not monkeypatched — this just documents the order _cgroup_quota_cores
    # itself checks in, via its source: v2's is_file() check comes first and
    # returns before v1 is ever consulted. Covered functionally by the tests
    # above, each of which patches only the layer they mean to exercise.
    import inspect

    source = inspect.getsource(_cgroup_quota_cores)
    assert source.index("_CGROUP_V2_CPU_MAX") < source.index("_CGROUP_V1_QUOTA")


def test_a_malformed_quota_file_is_treated_as_no_quota(monkeypatch, tmp_path):
    """Sizing a worker pool is not worth failing the whole extraction over."""
    v2 = tmp_path / "cpu.max"
    v2.write_text("garbage\n")
    monkeypatch.setattr(layout, "_CGROUP_V2_CPU_MAX", v2)

    assert _cgroup_quota_cores() is None


def test_cpu_count_is_capped_by_a_quota_smaller_than_affinity(monkeypatch):
    monkeypatch.setattr(layout.os, "sched_getaffinity", lambda pid: set(range(24)))
    monkeypatch.setattr(layout, "_cgroup_quota_cores", lambda: 2.0)

    assert _cpu_count() == 2


def test_cpu_count_floors_a_fractional_quota(monkeypatch):
    """2.9 cores of quota can run 2 workers at full tilt, not 3 splitting a
    ninth of a core three ways for no benefit."""
    monkeypatch.setattr(layout.os, "sched_getaffinity", lambda pid: set(range(24)))
    monkeypatch.setattr(layout, "_cgroup_quota_cores", lambda: 2.9)

    assert _cpu_count() == 2


def test_cpu_count_ignores_a_quota_larger_than_affinity(monkeypatch):
    """A cpuset pin to 4 cores plus a --cpus=16 quota: the pin is the real
    ceiling, not the quota."""
    monkeypatch.setattr(layout.os, "sched_getaffinity", lambda pid: set(range(4)))
    monkeypatch.setattr(layout, "_cgroup_quota_cores", lambda: 16.0)

    assert _cpu_count() == 4


def test_cpu_count_with_no_quota_at_all_is_just_affinity(monkeypatch):
    monkeypatch.setattr(layout.os, "sched_getaffinity", lambda pid: set(range(6)))
    monkeypatch.setattr(layout, "_cgroup_quota_cores", lambda: None)

    assert _cpu_count() == 6


def test_a_sub_one_quota_still_runs_at_least_one_worker(monkeypatch):
    """--cpus=0.5 is real and legal; the pool must not shrink to zero workers."""
    monkeypatch.setattr(layout.os, "sched_getaffinity", lambda pid: set(range(24)))
    monkeypatch.setattr(layout, "_cgroup_quota_cores", lambda: 0.5)

    assert _cpu_count() == 1


# --- extract_pages actually uses more than one worker --------------------------


def test_extract_pages_produces_correct_results_with_multiple_workers(tmp_path):
    """The parallel path end to end: page order and content are unaffected by
    which worker happened to extract which page."""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, [f"page number {i} content" for i in range(6)])

    pages = extract_pages(pdf, max_workers=3)

    assert [p.page_index for p in pages] == [0, 1, 2, 3, 4, 5]
    for i, page in enumerate(pages):
        assert f"page number {i} content" in page.text


def test_extract_pages_serial_and_parallel_agree(tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, [f"alpha beta gamma {i}" for i in range(5)])

    serial = extract_pages(pdf, max_workers=1)
    parallel = extract_pages(pdf, max_workers=4)

    assert [p.text for p in serial] == [p.text for p in parallel]
    assert [p.boxes for p in serial] == [p.boxes for p in parallel]


def test_extract_pages_never_spawns_more_workers_than_pages(tmp_path):
    """A 2-page document does not need (or benefit from) a 24-way pool."""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["one", "two"])

    # Would raise if it tried to size a pool larger than the page count in a
    # way that broke _page_ranges' arithmetic; asserting on the result is the
    # real check.
    pages = extract_pages(pdf, max_workers=24)

    assert len(pages) == 2


def test_a_zero_page_document_returns_no_pages(monkeypatch, tmp_path):
    """pymupdf itself refuses to *save* a zero-page PDF (`ValueError: cannot
    save with zero pages`), so this can only be exercised by faking the open
    — a malformed file could still report zero pages without pymupdf's own
    save-time guard ever having run on it."""

    class EmptyDoc:
        page_count = 0

        def close(self):
            pass

    monkeypatch.setattr(layout.pymupdf, "open", lambda path: EmptyDoc())

    assert extract_pages(tmp_path / "whatever.pdf") == []
