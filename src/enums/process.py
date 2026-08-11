from enum import Enum


class ProcessStatus(str, Enum):
    # Success statuses
    PROCESSING_STARTED = "processing started"
    PROCESSING_SUCCESS = "processing completed successfully"
    
    # Error statuses
    PROCESSING_FAILED = "processing failed"
    EXTRACTION_FAILED = "text extraction failed"
    CHUNKING_FAILED = "chunking failed"
    EMBEDDING_FAILED = "embedding failed"
    VECTOR_DB_ERROR = "vector database error"

