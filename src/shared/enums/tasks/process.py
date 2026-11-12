from enum import Enum


class ProcessStatus(str, Enum):
    PROCESSING_SUCCESS = "processing completed successfully"
    EXTRACTION_FAILED = "text extraction failed"
    CHUNKING_FAILED = "chunking failed"
