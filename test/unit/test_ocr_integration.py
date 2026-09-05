"""OCR in the ingestion path: when it runs, and what it costs when it does.

The decision is narrower than "OCR the document", and every narrowing here is a
page that would otherwise be re-read at seconds each for no gain — or, in the
highlight case, a citation that would point at the wrong sentence.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from controllers import ProcessController

ARABIC_GOOD = "اليسار حينئذ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان " * 3
ARABIC_FRAGMENTED = "ا ليسا ر حينئذ بديد و و معنا ه ا لها ر بة. و حد ث في أ يا م بيكماليو ن " * 3
ENGLISH = "The quick brown fox jumps over the lazy dog and keeps on running. " * 3


def _page(index, text, label=None):
    """Stands in for PdfLayoutController.PageWords."""
    return SimpleNamespace(page_index=index, text=text, page_label=label or str(index + 1))


@pytest.fixture
def controller(monkeypatch):
    controller = ProcessController()
    monkeypatch.setattr(controller.settings, "OCR_ENABLED", True)
    monkeypatch.setattr(controller.settings, "OCR_EXTRACTOR", "fake-ocr")
    return controller


@pytest.fixture
def fake_engine(monkeypatch):
    """Register a fake extractor so nothing here needs tesseract installed."""
    from ocr.base import ArabicExtractor

    class Fake(ArabicExtractor):
        name = "fake-ocr"
        calls: list = []

        def _extract(self, page):
            Fake.calls.append(page.number)
            return "نص أعيدت قراءته بواسطة محرك التعرف الضوئي على الحروف"

    Fake.calls = []
    monkeypatch.setattr("ocr.registry.ALL_EXTRACTORS", (Fake,))
    return Fake


def test_a_healthy_arabic_page_is_not_re_read(controller, fake_engine, tmp_path):
    """The text layer is the characters the author typed. OCR trades those for
    a guess at the pixels — strictly worse, as well as seconds slower."""
    pages = [_page(0, ARABIC_GOOD)]

    replacements = controller._reread_unusable_arabic(tmp_path / "x.pdf", pages)

    assert replacements == {}
    assert fake_engine.calls == []


def test_english_is_never_re_read(controller, fake_engine, tmp_path):
    """However badly segmented. An Arabic engine on English makes it worse."""
    pages = [_page(0, ENGLISH.replace(" ", "  "))]

    assert controller._reread_unusable_arabic(tmp_path / "x.pdf", pages) == {}
    assert fake_engine.calls == []


def test_an_unusable_arabic_page_is_re_read(controller, fake_engine, tmp_path):
    pages = [_page(0, ARABIC_FRAGMENTED)]

    replacements = controller._reread_unusable_arabic(tmp_path / "x.pdf", pages)

    assert fake_engine.calls == [0]
    assert "أعيدت" in replacements[0]


def test_only_the_broken_pages_are_re_read(controller, fake_engine, tmp_path):
    """Per page, not per document: a book with three good pages and one bad one
    pays for one page of OCR, not four."""
    pages = [
        _page(0, ARABIC_GOOD),
        _page(1, ARABIC_FRAGMENTED),
        _page(2, ARABIC_GOOD),
        _page(3, ARABIC_FRAGMENTED),
    ]

    replacements = controller._reread_unusable_arabic(tmp_path / "x.pdf", pages)

    assert sorted(fake_engine.calls) == [1, 3]
    assert sorted(replacements) == [1, 3]


def test_a_short_page_is_left_alone(controller, fake_engine, tmp_path):
    """A plate or a chapter heading has no spacing statistics worth judging,
    and would otherwise be re-read for nothing."""
    pages = [_page(0, "تقرير سنوي")]

    assert controller._reread_unusable_arabic(tmp_path / "x.pdf", pages) == {}
    assert fake_engine.calls == []


def test_nothing_happens_when_ocr_is_disabled(controller, fake_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(controller.settings, "OCR_ENABLED", False)

    assert controller._reread_unusable_arabic(tmp_path / "x.pdf", [_page(0, ARABIC_FRAGMENTED)]) == {}
    assert fake_engine.calls == []


# --- the part a user would notice ---------------------------------------------


def test_an_ocred_page_keeps_its_boxes_and_records_a_scale(controller, fake_engine, tmp_path):
    """OCR produces no coordinates, so the word boxes remain the only positional
    information about the page. They are kept, along with the length ratio
    between the two renderings, so offsets from one can be mapped onto the
    other — see highlight_metadata, which marks the result approximate."""
    controller._pdf_pages = {0: _page(0, ARABIC_FRAGMENTED), 1: _page(1, ARABIC_GOOD)}
    pages = [controller._pdf_pages[0], controller._pdf_pages[1]]

    controller._reread_unusable_arabic(tmp_path / "x.pdf", pages)

    assert 0 in controller._pdf_pages, "the re-read page lost the only boxes it had"
    assert controller._ocr_scale[0] > 0, "no scale recorded, so offsets cannot be mapped"
    assert 1 not in controller._ocr_scale, "an untouched page should need no scaling"


def test_a_scaled_highlight_is_marked_approximate():
    """A reader has to be able to tell an exact highlight from a close one, and
    a future change has to be able to find the close ones."""
    from types import SimpleNamespace

    from controllers.PdfLayoutController import highlight_metadata

    page = SimpleNamespace(
        width=100.0, height=100.0,
        starts=[0, 10, 20],
        words=["x" * 10] * 3,
        # pymupdf word tuples: x0, y0, x1, y1, block, line, word_no. Same block
        # and line with consecutive word numbers, so the three merge into one
        # rectangle rather than three.
        boxes=[
            [0.0, 0.0, 9.0, 9.0, 0, 0, 0],
            [10.0, 0.0, 19.0, 9.0, 0, 0, 1],
            [20.0, 0.0, 29.0, 9.0, 0, 0, 2],
        ],
        text="x" * 30,
    )

    exact = highlight_metadata(page, 0, 30)
    scaled = highlight_metadata(page, 0, 15, scale=2.0)

    assert "approx" not in exact
    assert scaled["approx"] == 1
    # scale=2.0 turns 0-15 into 0-30, so both cover the same words.
    assert scaled["r"] == exact["r"]


def test_a_missing_engine_keeps_the_text_layer(controller, tmp_path, monkeypatch, caplog):
    """Failing an upload because an OCR binary is absent would be a worse
    outcome than indexing imperfect text."""
    monkeypatch.setattr("ocr.registry.ALL_EXTRACTORS", ())

    with caplog.at_level("WARNING"):
        replacements = controller._reread_unusable_arabic(
            tmp_path / "x.pdf", [_page(0, ARABIC_FRAGMENTED)]
        )

    assert replacements == {}
    assert any("cannot run" in record.message for record in caplog.records)


def test_a_failing_engine_leaves_that_page_alone(controller, tmp_path, monkeypatch, caplog):
    """One unreadable page must not lose the rest of the document."""
    from ocr.base import ArabicExtractor

    class Exploding(ArabicExtractor):
        name = "fake-ocr"

        def _extract(self, page):
            raise RuntimeError("the traineddata is corrupt")

    monkeypatch.setattr("ocr.registry.ALL_EXTRACTORS", (Exploding,))
    controller._pdf_pages = {0: _page(0, ARABIC_FRAGMENTED)}

    with caplog.at_level("WARNING"):
        replacements = controller._reread_unusable_arabic(
            tmp_path / "x.pdf", [controller._pdf_pages[0]]
        )

    assert replacements == {}
    # The OCR did not happen, so the boxes still describe the text.
    assert 0 in controller._pdf_pages
    assert any("OCR failed" in record.message for record in caplog.records)


# --- the constraint the Celery migration introduced ----------------------------


def test_page_extraction_does_not_fork_inside_a_daemonic_worker(monkeypatch):
    """A Celery prefork worker is daemonic, and a daemonic process may not have
    children — `ProcessPoolExecutor` raises "daemonic processes are not allowed
    to have children" as soon as it starts one.

    Ingestion runs there now, so every PDF upload failed with an ExtractionError
    that named the temp file and not the cause. This pins the serial fallback:
    without it the pool is constructed and the upload dies.
    """
    import controllers.PdfLayoutController as layout

    monkeypatch.setattr(layout, "_is_daemonic", lambda: True)
    monkeypatch.setattr(layout, "_cpu_count", lambda: 24)

    def explode(*args, **kwargs):
        raise AssertionError("daemonic processes are not allowed to have children")

    monkeypatch.setattr(layout, "ProcessPoolExecutor", explode)

    pdf = _arabic_pdf()

    pages = layout.extract_pages(pdf)

    # Completing at all is the assertion: with the pool, this raises. The
    # fixture is rendered from an image and so carries no text layer, which is
    # beside the point here and is what `_reread_unusable_arabic` is for.
    assert len(pages) == 2
    assert [page.page_index for page in pages] == [0, 1]


def test_page_extraction_still_forks_when_it_may(monkeypatch, tmp_path):
    """The constraint belongs to the caller's context, not to this module: the
    API process and the benchmark still get the pool."""
    import controllers.PdfLayoutController as layout

    monkeypatch.setattr(layout, "_is_daemonic", lambda: False)
    monkeypatch.setattr(layout, "_cpu_count", lambda: 4)

    used = []
    real_pool = layout.ProcessPoolExecutor

    def spy(*args, **kwargs):
        used.append(True)
        return real_pool(*args, **kwargs)

    monkeypatch.setattr(layout, "ProcessPoolExecutor", spy)

    layout.extract_pages(_arabic_pdf())

    assert used, "the process pool was skipped when it was allowed"


def _arabic_pdf() -> Path:
    """A real two-page PDF with Arabic text, rendered once and cached."""
    import tempfile

    from PIL import Image, ImageDraw, ImageFont

    cached = Path(tempfile.gettempdir()) / "ocr-daemonic-test.pdf"

    if cached.is_file():
        return cached

    font_path = "/usr/share/fonts/google-noto-vf/NotoNaskhArabic[wght].ttf"

    if not Path(font_path).is_file():
        pytest.skip("no Arabic font available to build the fixture")

    pages = []
    for line in ("تقرير سنوي عن حالة المكتبة", "البند الأول مراجعة البيانات"):
        image = Image.new("RGB", (900, 300), "white")
        ImageDraw.Draw(image).text(
            (860, 100), line, font=ImageFont.truetype(font_path, 40),
            fill="black", anchor="ra", direction="rtl", language="ar",
        )
        pages.append(image)

    pages[0].save(cached, "PDF", save_all=True, append_images=pages[1:])

    return cached


# --- using the machine it is running on ----------------------------------------


def test_pages_are_read_concurrently(controller, tmp_path, monkeypatch):
    """OCR is the slowest step in ingestion and every page is independent, so
    the pages of one document are read in parallel.

    Threads rather than processes: a Celery prefork worker is daemonic and may
    not fork (see the daemonic test above), and tesseract runs as a subprocess
    anyway, so the GIL is released for the whole of the work.
    """
    import threading

    from ocr.base import ArabicExtractor

    seen: set[int] = set()
    barrier = threading.Barrier(4, timeout=10)

    class Concurrent(ArabicExtractor):
        name = "fake-ocr"

        def _extract(self, page):
            seen.add(threading.get_ident())
            # Blocks until four pages are being read at once; times out and
            # raises if they are being read one at a time.
            barrier.wait()
            return "نص أعيدت قراءته بواسطة محرك التعرف الضوئي على الحروف"

    monkeypatch.setattr("ocr.registry.ALL_EXTRACTORS", (Concurrent,))
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 4)

    pages = [_page(n, ARABIC_FRAGMENTED) for n in range(4)]
    controller._pdf_pages = {page.page_index: page for page in pages}

    replacements = controller._reread_unusable_arabic(tmp_path / "x.pdf", pages)

    assert sorted(replacements) == [0, 1, 2, 3]
    assert len(seen) > 1, "every page was read on the same thread"


def test_the_pool_is_never_wider_than_the_work(controller, fake_engine, tmp_path, monkeypatch):
    """Two pages must not start twenty-four threads."""
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 24)

    assert controller._ocr_workers(2) == 2
    assert controller._ocr_workers(0) == 1


def test_worker_count_falls_back_to_the_available_cpus(controller, monkeypatch):
    """0 means "use what this process may actually use" — which inside a
    container is the cgroup quota, not the host's core count."""
    import sys

    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    # The class is exported under the module's own name, so the dotted string
    # form resolves to the class. Reach for the module itself.
    monkeypatch.setattr(sys.modules["controllers.ProcessController"], "_cpu_count", lambda: 2)

    assert controller._ocr_workers(50) == 2


def test_tesseract_internal_threading_is_capped(controller, fake_engine, tmp_path, monkeypatch):
    """Tesseract's own OpenMP threads stacked under the page pool oversubscribe
    every core and run slower than either level of parallelism alone."""
    import os

    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)

    controller._reread_unusable_arabic(tmp_path / "x.pdf", [_page(0, ARABIC_FRAGMENTED)])

    assert os.environ["OMP_THREAD_LIMIT"] == "1"


def test_worker_count_is_capped_by_memory_not_just_cpus(controller, monkeypatch):
    """The bug this pins killed a real ingestion. Defaulting to the CPU count
    alone started 24 tesseract processes at ~200 MB each on a host with 5 GB
    free; the kernel OOM-killed the celery worker mid-document
    (`WorkerLostError: signal 9`) and the retry made it look merely slow.
    """
    import sys

    module = sys.modules["controllers.ProcessController"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(module, "_cpu_count", lambda: 24)
    monkeypatch.setattr(module, "_available_memory_mb", lambda: 5120.0)

    # Half of 5 GB, at 256 MB a page.
    assert controller._ocr_workers(222) == 10


def test_an_explicit_worker_count_is_still_capped_by_memory(controller, monkeypatch):
    """The setting says how much parallelism is wanted, not how much the box
    can survive."""
    import sys

    module = sys.modules["controllers.ProcessController"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 64)
    monkeypatch.setattr(module, "_cpu_count", lambda: 64)
    monkeypatch.setattr(module, "_available_memory_mb", lambda: 1024.0)

    assert controller._ocr_workers(222) == 2


def test_at_least_one_page_is_read_even_on_a_tiny_host(controller, monkeypatch):
    """Refusing to OCR at all because memory is tight would be worse than
    doing it one page at a time."""
    import sys

    module = sys.modules["controllers.ProcessController"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(module, "_cpu_count", lambda: 8)
    monkeypatch.setattr(module, "_available_memory_mb", lambda: 64.0)

    assert controller._ocr_workers(222) == 1


def test_unknowable_memory_falls_back_to_the_cpu_count(controller, monkeypatch):
    """On a platform where neither cgroup nor /proc/meminfo can be read, the
    CPU bound is still applied rather than nothing."""
    import sys

    module = sys.modules["controllers.ProcessController"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(module, "_cpu_count", lambda: 4)
    monkeypatch.setattr(module, "_available_memory_mb", lambda: None)

    assert controller._ocr_workers(222) == 4
