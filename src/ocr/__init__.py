"""Arabic text extraction: is this page Arabic, is its text layer usable, and
if not, which engine should re-read it.

The package exists because this project's Arabic PDFs are not an OCR problem in
the usual sense. They *have* text layers. The text is simply wrong in ways a
character count does not reveal: glyph-position extraction splits `اليسار` into
`ا ليسا ر`, and other producers run whole clauses together as `وحدثفي`. Both
are real Arabic in real codepoints, and neither can be searched.

So the decision is three-way, not two — use the text layer, normalise it, or
throw it away and re-read the pixels — and which is right varies per document.
`language` answers the first two questions cheaply; `benchmark` measures the
engines that answer the third.
"""

from .base import ArabicExtractor, Extraction, Page
from .language import ScriptProfile, is_arabic, normalize, profile
from .pipeline import ArabicOcrPipeline, PipelineDecision, configured_pipeline
from .registry import build, survey, unavailable

__all__ = [
    "ArabicExtractor",
    "ArabicOcrPipeline",
    "Extraction",
    "Page",
    "PipelineDecision",
    "ScriptProfile",
    "build",
    "configured_pipeline",
    "is_arabic",
    "normalize",
    "profile",
    "survey",
    "unavailable",
]
