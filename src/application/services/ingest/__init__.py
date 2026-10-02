"""Getting a document in: bytes on disk to chunks in the database.

The pipeline in order — `DataService` validates what was uploaded,
`AssetIngestService` decides whether the notebook already holds it and
queues the chain that ingests it, `FileService` puts it somewhere,
`ProcessService` loads it (and drives OCR when a page has no text layer),
`TextCorrectionService` hands each page to a local model to undo the damage
the text layer did to it, `TextProcessingService` normalises and splits what
comes out, and
`PdfLayoutService` keeps the word coordinates that let a chunk be
highlighted back on its page. `UrlSourceService` is the other way in: a
link instead of an upload, turned into the bytes the rest of the path expects.

They are grouped because they are one path with one failure surface: a change
to how text is split is usually a change to how it is located again.
"""

from .AssetIngestService import AssetIngestService, QueuedIngestion
from .DataService import DataService
from .PdfLayoutService import (
    extract_page_range,
    highlight_metadata,
    page_count,
    page_from_dict,
    page_to_dict,
)
from .ProcessService import ProcessService
from .TextCorrectionService import (
    CorrectedPage,
    CorrectionSet,
    TextCorrectionService,
)
from .TextProcessingService import (
    TextProcessingService,
    normalize_text,
    strip_nulls,
)
from .UrlSourceService import (
    FetchedSource,
    UrlSourceService,
    google_sheet_reference,
    transcript_page,
    youtube_video_id,
)

__all__ = [
    "AssetIngestService",
    "CorrectedPage",
    "CorrectionSet",
    "DataService",
    "FetchedSource",
    "extract_page_range",
    "ProcessService",
    "TextCorrectionService",
    "TextProcessingService",
    "highlight_metadata",
    "normalize_text",
    "page_count",
    "page_from_dict",
    "page_to_dict",
    "QueuedIngestion",
    "strip_nulls",
    "transcript_page",
    "UrlSourceService",
    "google_sheet_reference",
    "youtube_video_id",
]
