from enum import Enum


class FileExtension(str, Enum):
    TXT = ".txt"
    PDF = ".pdf"
    # for later usage
    DOC = ".doc"
    DOCX = ".docx"
    MD = ".md"
    JSON = ".json"
    CSV = ".csv"
    PY = ".py"
   