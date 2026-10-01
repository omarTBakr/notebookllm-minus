"""Every extractor, grouped by how it reads a page."""

from .hosted import GeminiExtractor, OpenRouterExtractor
from .qalam_extractor import QalamExtractor
from .remote import QariRemoteExtractor
from .text_layer import (
    PdfPlumberExtractor,
    PyMuPDFRawExtractor,
    PyMuPDFWordsExtractor,
)
from .traditional import (
    EasyOCRExtractor,
    PaddleOCRExtractor,
    SuryaExtractor,
    TesseractBestExtractor,
    TesseractExtractor,
)
from .vision_models import QariExtractor

#: Declaration order is report order: cheapest and most conventional first, so
#: a table reads as an escalation from "free" to "metered".
ALL_EXTRACTORS = (
    PyMuPDFRawExtractor,
    PyMuPDFWordsExtractor,
    PdfPlumberExtractor,
    QalamExtractor,
    TesseractExtractor,
    TesseractBestExtractor,
    EasyOCRExtractor,
    PaddleOCRExtractor,
    SuryaExtractor,
    QariExtractor,
    QariRemoteExtractor,
    GeminiExtractor,
    OpenRouterExtractor,
)

__all__ = [
    "ALL_EXTRACTORS",
    "EasyOCRExtractor",
    "GeminiExtractor",
    "OpenRouterExtractor",
    "PaddleOCRExtractor",
    "PdfPlumberExtractor",
    "PyMuPDFRawExtractor",
    "PyMuPDFWordsExtractor",
    "QalamExtractor",
    "QariExtractor",
    "QariRemoteExtractor",
    "SuryaExtractor",
    "TesseractBestExtractor",
    "TesseractExtractor",
]
