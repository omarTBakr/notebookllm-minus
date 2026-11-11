"""OCR in the ingestion path: what it costs when it runs.

How many pages are read at once, the approximate highlight a replaced page
keeps, and the fork constraint a Celery worker puts on page extraction. Which
pages get OCR'd is test_ocr_unreadable_pages.py.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from application.services import ProcessService


@pytest.fixture
def controller():
    return ProcessService()


# --- the part a user would notice ---------------------------------------------


def test_a_scaled_highlight_is_marked_approximate():
    """A reader has to be able to tell an exact highlight from a close one, and
    a future change has to be able to find the close ones."""
    from application.services.ingest.PdfLayoutService import highlight_metadata

    page = SimpleNamespace(
        width=100.0,
        height=100.0,
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


# --- the constraint the Celery migration introduced ----------------------------


def test_page_extraction_does_not_fork_inside_a_daemonic_worker(monkeypatch):
    """A Celery prefork worker is daemonic, and a daemonic process may not have
    children — `ProcessPoolExecutor` raises "daemonic processes are not allowed
    to have children" as soon as it starts one.

    Ingestion runs there now, so every PDF upload failed with an ExtractionError
    that named the temp file and not the cause. This pins the serial fallback:
    without it the pool is constructed and the upload dies.
    """
    import application.services.ingest.PdfLayoutService as layout

    monkeypatch.setattr(layout, "is_daemonic", lambda: True)
    monkeypatch.setattr(layout, "cpu_count", lambda: 24)

    def explode(*args, **kwargs):
        raise AssertionError("daemonic processes are not allowed to have children")

    monkeypatch.setattr(layout, "ProcessPoolExecutor", explode)

    pdf = _arabic_pdf()

    pages = layout.extract_pages(pdf)

    # Completing at all is the assertion: with the pool, this raises. The
    # fixture is rendered from an image and so carries no text layer, which is
    # beside the point here.
    assert len(pages) == 2
    assert [page.page_index for page in pages] == [0, 1]


def test_page_extraction_still_forks_when_it_may(monkeypatch, tmp_path):
    """The constraint belongs to the caller's context, not to this module: the
    API process and the benchmark still get the pool."""
    import application.services.ingest.PdfLayoutService as layout

    monkeypatch.setattr(layout, "is_daemonic", lambda: False)
    monkeypatch.setattr(layout, "cpu_count", lambda: 4)

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
            (860, 100),
            line,
            font=ImageFont.truetype(font_path, 40),
            fill="black",
            anchor="ra",
            direction="rtl",
            language="ar",
        )
        pages.append(image)

    pages[0].save(cached, "PDF", save_all=True, append_images=pages[1:])

    return cached


# --- using the machine it is running on ----------------------------------------


def test_the_pool_is_never_wider_than_the_work(controller, monkeypatch):
    """Two pages must not start twenty-four threads."""
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 24)

    assert controller._ocr_workers(2) == 2
    assert controller._ocr_workers(0) == 1


def test_worker_count_falls_back_to_the_available_cpus(controller, monkeypatch):
    """0 means "use what this process may actually use" — which inside a
    container is the cgroup quota, not the host's core count."""
    import sys

    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    # Concurrency 1 so this isolates the CPU bound; dividing the machine
    # between concurrent tasks has its own tests below.
    monkeypatch.setattr(controller.settings, "CELERY_WORKER_CONCURRENCY", 1)
    # The class is exported under the module's own name, so the dotted string
    # form resolves to the class. Reach for the module itself.
    monkeypatch.setattr(sys.modules["application.services.ingest.ProcessService"], "cpu_count", lambda: 2)

    assert controller._ocr_workers(50) == 2


def test_worker_count_is_capped_by_memory_not_just_cpus(controller, monkeypatch):
    """The bug this pins killed a real ingestion. Defaulting to the CPU count
    alone started 24 tesseract processes at ~200 MB each on a host with 5 GB
    free; the kernel OOM-killed the celery worker mid-document
    (`WorkerLostError: signal 9`) and the retry made it look merely slow.
    """
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    # Pinned, because this test is about the *memory* cap and the other bound
    # would otherwise decide it: CELERY_WORKER_CONCURRENCY now defaults to 0
    # ("every available CPU"), which divides the per-task core share down to 1
    # and would mask what is being measured here.
    monkeypatch.setattr(controller.settings, "CELERY_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(module, "cpu_count", lambda: 24)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 5120.0)

    # Half of 5 GB, at 256 MB a page.
    assert controller._ocr_workers(222) == 10


def test_an_explicit_worker_count_is_honoured_above_the_memory_bound(controller, monkeypatch, caplog):
    """An operator who sets this has the machine in front of them.

    Capping it silently is what left a 2-vCPU server unable to use both cores:
    OCR ran single-threaded, 214 pages cost 428s of a 540s budget, and every
    upload of that book died on the soft time limit with the document left
    showing zero chunks. The bound still has something to say, so it says it.
    """
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 4)
    monkeypatch.setattr(module, "cpu_count", lambda: 64)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 1024.0)

    with caplog.at_level("WARNING"):
        assert controller._ocr_workers(222) == 4

    assert any("above what free memory suggests" in r.message for r in caplog.records)


def test_an_explicit_worker_count_within_the_bound_warns_about_nothing(controller, monkeypatch, caplog):
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 2)
    monkeypatch.setattr(module, "cpu_count", lambda: 64)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 4096.0)

    with caplog.at_level("WARNING"):
        assert controller._ocr_workers(222) == 2

    assert not [r for r in caplog.records if "free memory" in r.message]


def test_at_least_one_page_is_read_even_on_a_tiny_host(controller, monkeypatch):
    """Refusing to OCR at all because memory is tight would be worse than
    doing it one page at a time."""
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(module, "cpu_count", lambda: 8)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 64.0)

    assert controller._ocr_workers(222) == 1


def test_unknowable_memory_falls_back_to_the_cpu_count(controller, monkeypatch):
    """On a platform where neither cgroup nor /proc/meminfo can be read, the
    CPU bound is still applied rather than nothing."""
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(controller.settings, "CELERY_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(module, "cpu_count", lambda: 4)
    monkeypatch.setattr(module, "available_memory_mb", lambda: None)

    assert controller._ocr_workers(222) == 4


def test_the_pool_is_divided_by_worker_concurrency(controller, monkeypatch):
    """A prefork worker runs CELERY_WORKER_CONCURRENCY tasks at once and each
    sizes its own pool, so a per-task pool of `cpus` over-subscribes the box by
    exactly that factor.

    Observed on a 4-core server with concurrency 2: two documents ingesting
    together put 8 tesseract processes on 4 cores, load average 8.66, six
    processes each getting ~48% of a core instead of three getting ~100%.
    Aggregate CPU looked unremarkable in a dashboard because the time went into
    scheduling rather than work.
    """
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(controller.settings, "CELERY_WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(module, "cpu_count", lambda: 4)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 8192.0)

    assert controller._ocr_workers(214) == 2, "4 cores over 2 concurrent tasks is 2 each"


def test_a_single_task_worker_gets_every_core(controller, monkeypatch):
    """The other half of the same rule: at concurrency 1 nothing else is
    competing, so one document should use the whole machine."""
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(controller.settings, "CELERY_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(module, "cpu_count", lambda: 4)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 8192.0)

    assert controller._ocr_workers(214) == 4


def test_the_share_never_falls_below_one(controller, monkeypatch):
    """Concurrency above the core count must not round the pool down to zero."""
    import sys

    module = sys.modules["application.services.ingest.ProcessService"]
    monkeypatch.setattr(controller.settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(controller.settings, "CELERY_WORKER_CONCURRENCY", 8)
    monkeypatch.setattr(module, "cpu_count", lambda: 4)
    monkeypatch.setattr(module, "available_memory_mb", lambda: 8192.0)

    assert controller._ocr_workers(214) == 1
