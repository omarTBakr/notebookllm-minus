"""Choose between a PDF text layer and Arabic OCR."""

from __future__ import annotations

from dataclasses import dataclass

from .base import ArabicExtractor, Page
from .language import ScriptProfile, normalize, profile
from .registry import build, survey


@dataclass(frozen=True)
class PipelineDecision:
    """The evidence used to choose the extraction path."""

    text: str
    profile: ScriptProfile
    used_ocr: bool
    extractor: str


class ArabicOcrPipeline:
    """Use a usable text layer, otherwise delegate to one OCR extractor.

    The pipeline never OCRs healthy non-Arabic text. For Arabic, it treats
    fragmented or run-together text as unusable and requires a named extractor
    so benchmark and production choices remain explicit.
    """

    def __init__(self, extractor: ArabicExtractor | None = None) -> None:
        self.extractor = extractor

    def _text_layer(self, page: Page) -> tuple[str, ScriptProfile]:
        text = normalize(page.text_layer)
        return text, profile(text)

    def extract(self, page: Page) -> PipelineDecision:
        text, details = self._text_layer(page)

        if not details.is_arabic or details.is_usable:
            return PipelineDecision(
                text=text,
                profile=details,
                used_ocr=False,
                extractor="text-layer",
            )

        if self.extractor is None:
            raise RuntimeError("Arabic text layer is unusable; configure an available OCR extractor")

        result = self.extractor.run(page)
        if not result.ok:
            raise RuntimeError(f"OCR extractor {self.extractor.name!r} failed: {result.error or 'empty output'}")

        ocr_text = normalize(result.text)
        return PipelineDecision(
            text=ocr_text,
            profile=profile(ocr_text),
            used_ocr=True,
            extractor=self.extractor.name,
        )


def configured_pipeline(name: str) -> ArabicOcrPipeline:
    """Build a pipeline from the registry, failing with an actionable message."""
    extractors = build([name])
    if not extractors:
        available = {entry.name: entry.reason for entry in survey()}
        raise RuntimeError(f"OCR extractor {name!r} is unavailable: {available.get(name, 'unknown extractor')}")
    return ArabicOcrPipeline(extractors[0])
