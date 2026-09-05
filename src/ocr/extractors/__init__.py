"""Every extractor, grouped by how it reads a page."""

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
from .vision_models import GeminiExtractor, QariExtractor

#: Declaration order is report order: cheapest and most conventional first, so
#: a table reads as an escalation from "free" to "metered".
ALL_EXTRACTORS = (
    PyMuPDFRawExtractor,
    PyMuPDFWordsExtractor,
    PdfPlumberExtractor,
    TesseractExtractor,
    TesseractBestExtractor,
    EasyOCRExtractor,
    PaddleOCRExtractor,
    SuryaExtractor,
    QariExtractor,
    QariRemoteExtractor,
    GeminiExtractor,
)

__all__ = [
    "ALL_EXTRACTORS",
    "EasyOCRExtractor",
    "GeminiExtractor",
    "PaddleOCRExtractor",
    "PdfPlumberExtractor",
    "PyMuPDFRawExtractor",
    "PyMuPDFWordsExtractor",
    "QariExtractor",
    "QariRemoteExtractor",
    "SuryaExtractor",
    "TesseractBestExtractor",
    "TesseractExtractor",
]
