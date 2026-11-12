"""Finding the extractors that can actually run here.

Availability is a runtime question, not a packaging one: tesseract needs a
binary and a language pack, Qari needs a CUDA device, Gemini needs a key. Each
extractor answers for itself and says why when the answer is no, so a comparison
table can distinguish "this engine did badly" from "this engine never ran" —
the two things a bare absence gets misread as.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import ArabicExtractor
from .extractors import ALL_EXTRACTORS


@dataclass
class Availability:
    extractor: type[ArabicExtractor]
    ok: bool
    reason: str

    @property
    def name(self) -> str:
        return self.extractor.name


def survey(names: list[str] | None = None) -> list[Availability]:
    """Every known extractor and whether it can run, in report order.

    Narrowed to *names* when given, and then only those are probed. A probe
    is not free -- some import torch, look for a CUDA device or make an HTTP
    call -- so the application, which wants one engine, must not pay for the
    other twelve on every upload. The benchmark passes nothing and gets all.
    """
    wanted = set(names) if names else None
    results = []

    for extractor in ALL_EXTRACTORS:
        if wanted is not None and extractor.name not in wanted:
            continue

        try:
            ok, reason = extractor.available()
        except Exception as exc:  # noqa: BLE001 - a broken probe is unavailable
            ok, reason = False, f"availability check raised {type(exc).__name__}: {exc}"

        results.append(Availability(extractor, ok, reason))

    return results


def build(names: list[str] | None = None, **options) -> list[ArabicExtractor]:
    """Instantiate the available extractors, optionally narrowed to *names*."""
    return [entry.extractor(**options.get(entry.name, {})) for entry in survey(names) if entry.ok]


def unavailable() -> list[Availability]:
    return [entry for entry in survey() if not entry.ok]
