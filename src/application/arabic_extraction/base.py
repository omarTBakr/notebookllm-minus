"""The contract every Arabic text extractor implements.

Two kinds of thing sit behind this interface and they are not alike. A text-layer
extractor reads codepoints the PDF already contains — nearly free, and correct
whenever the producer embedded sane text. An OCR engine re-reads the rendered
page as an image — orders of magnitude more expensive, and the only option when
the text layer is absent or mangled.

They share an interface because the *choice* between them is the interesting
part, and a fair comparison needs them measured the same way: same page, same
timer, same quality signals. Anything that reads a page into Arabic text can be
dropped in and benchmarked against the rest without the harness knowing what it
is.

`Page` carries both representations lazily. An extractor takes whichever it
needs, and a page is rendered at most once however many engines ask for it —
rasterising a 300-dpi page costs more than some of the extractors do.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from .language import ScriptProfile, normalize, profile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL.Image import Image


# 300 dpi is the floor most OCR engines are trained around; below it Arabic
# diacritics and the dots that distinguish ب ت ث start disappearing into the
# same pixel. Higher costs memory quadratically for little gain.
DEFAULT_DPI = 300


@dataclass
class Page:
    """One page, available as either a text layer or an image.

    Both are computed on demand and cached, so a benchmark running twelve
    extractors over one page renders it once.
    """

    path: Path
    number: int  # 0-based, as pymupdf counts
    dpi: int = DEFAULT_DPI

    @cached_property
    def _document(self):
        import pymupdf

        return pymupdf.open(self.path)

    @cached_property
    def text_layer(self) -> str:
        """What the PDF says it contains. Empty for a scan."""
        return self._document[self.number].get_text() or ""

    @cached_property
    def image(self) -> "Image":
        """The page as it is drawn, for an engine that must look at it."""
        import io

        from PIL import Image as PILImage

        pixmap = self._document[self.number].get_pixmap(dpi=self.dpi)

        return PILImage.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")

    @cached_property
    def png_bytes(self) -> bytes:
        """The rendered page as PNG, for engines and APIs that take bytes."""
        import io

        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG")

        return buffer.getvalue()

    @property
    def has_text_layer(self) -> bool:
        return len(self.text_layer.strip()) > 20

    def close(self) -> None:
        if "_document" in self.__dict__:
            self._document.close()


@dataclass
class Extraction:
    """What one extractor made of one page, and what it cost."""

    extractor: str
    text: str
    seconds: float
    page: int
    error: str = ""
    metadata: dict = field(default_factory=dict)
    #: Process high-water RSS — the engine's footprint, model included. Only
    #: meaningful because the harness runs one engine per process.
    peak_rss_mb: float | None = None
    #: Growth across this one call: whether the engine leaks between pages.
    rss_delta_mb: float | None = None
    peak_gpu_mb: float | None = None
    cpu_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text.strip())

    @cached_property
    def normalized(self) -> str:
        """The text as the index would store it.

        Comparisons are made on this rather than the raw output, because an
        engine is not better for emitting presentation forms that the pipeline
        folds away anyway — and would look better on a naive character diff.
        """
        return normalize(self.text)

    @cached_property
    def profile(self) -> ScriptProfile:
        return profile(self.normalized)


class ArabicExtractor(ABC):
    """Turns a page into Arabic text.

    Subclasses implement `_extract`; `run` wraps it with timing and error
    capture so a benchmark comparing a dozen engines is not twelve
    try/excepts, and so one engine failing to install never stops the rest.
    """

    #: Short, stable identifier used in results tables and to select engines.
    name: str = ""

    #: What it is, in a few words, for the comparison report.
    description: str = ""

    #: True when it reads the PDF's own text; False when it re-reads pixels.
    reads_text_layer: bool = False

    #: Whether a GPU materially changes whether this is usable.
    wants_gpu: bool = False

    def __init__(self, **options) -> None:
        self.options = options

    # --- what a subclass provides --------------------------------------------

    @abstractmethod
    def _extract(self, page: Page) -> str:
        """Return the page's Arabic text. Raise on failure; `run` catches it."""

    @classmethod
    def available(cls) -> tuple[bool, str]:
        """Whether this can run here, and why not when it cannot.

        Returning a reason rather than a bare False is the point: "surya is not
        installed" and "surya is installed but there is no GPU" are different
        answers, and a comparison table that shows neither is the one people
        misread as "this engine is bad".
        """
        return True, ""

    def warm_up(self) -> None:
        """Load models before timing starts.

        Without this the first page of a run carries the model load — often
        tens of seconds for a VLM — and the per-page cost reported for that
        engine is wrong by an order of magnitude.
        """

    # --- what the harness calls ----------------------------------------------

    def run(self, page: Page) -> Extraction:
        """Extract one page, timed and measured.

        Memory is the part that is easy to report wrongly, and was. Two
        different numbers are needed and only one of them is a delta:

        `peak_rss_mb` is the *process* high-water mark, which is the engine's
        real footprint — model weights included — and is only meaningful
        because the harness runs one engine per process. Subtracting a
        before-reading from an after-reading does not measure an engine at all:
        CPython does not return freed pages to the OS, so whichever engine
        loads first is charged for memory every later engine then reuses for
        free, and an engine whose model was loaded in `warm_up` shows a delta
        of nearly zero however large it is.

        `rss_delta_mb` keeps that per-call growth, which answers a narrower
        question — does this engine leak across pages — and answers it well.

        GPU memory is genuinely per-extraction: the peak counter is reset
        immediately before the call, and only for engines that use a GPU, so
        CUDA is never initialised on behalf of an engine that does not.
        """
        started = time.perf_counter()
        cpu_started = time.process_time()
        rss_before = self._rss_mb()

        if self.wants_gpu:
            self._reset_gpu_peak()

        try:
            text = self._extract(page)
            error = ""
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            text, error = "", f"{type(exc).__name__}: {exc}"

        rss_after = self._rss_mb()
        rss_delta_mb = max(0.0, rss_after - rss_before) if rss_after is not None and rss_before is not None else None

        peak_gpu_mb = None
        if self.wants_gpu:
            try:
                import torch

                if torch.cuda.is_available():
                    peak_gpu_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
            except (ImportError, RuntimeError):
                pass

        return Extraction(
            extractor=self.name,
            text=text or "",
            seconds=time.perf_counter() - started,
            page=page.number,
            error=error,
            peak_rss_mb=self._process_peak_rss_mb(),
            rss_delta_mb=rss_delta_mb,
            peak_gpu_mb=peak_gpu_mb,
            cpu_seconds=time.process_time() - cpu_started,
        )

    @staticmethod
    def _process_peak_rss_mb() -> float | None:
        """The process's high-water RSS, in MB.

        Never decreases, which is exactly what makes it the right number here
        and the wrong one in a combined run: with one engine per process it is
        that engine's footprint, and in a shared process it is everyone's.
        """
        try:
            import resource
        except ImportError:  # pragma: no cover - not POSIX
            return None

        # ru_maxrss is kilobytes on Linux, bytes on macOS.
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        return peak / 1024 if sys.platform != "darwin" else peak / (1024 * 1024)

    @staticmethod
    def _rss_mb() -> float | None:
        try:
            import psutil

            return psutil.Process().memory_info().rss / (1024 * 1024)
        except ImportError:
            return None

    @staticmethod
    def _reset_gpu_peak() -> None:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except (ImportError, RuntimeError):
            pass

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name!r}>"
