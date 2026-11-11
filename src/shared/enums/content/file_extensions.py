from enum import Enum


class FileExtension(str, Enum):
    TXT = ".txt"
    PDF = ".pdf"
    XLSX = ".xlsx"
    MD = ".md"
    CSV = ".csv"


class PdfLoader(str, Enum):
    """Which library extracts text from a PDF.

    They are not interchangeable on right-to-left text — see the measurements
    against PDF_LOADER in utils/config.py before changing this.
    """

    PYPDF = "pypdf"  # fast, correct RTL order, drops some glyphs
    PDFPLUMBER = "pdfplumber"  # better Latin tables; RTL comes out REVERSED
    PYMUPDF = "pymupdf"  # loses no glyphs, ~19x slower
