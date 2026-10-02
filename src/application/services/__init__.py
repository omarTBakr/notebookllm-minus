"""The project's services, grouped by what they are responsible for.

Everything is re-exported here, and callers import from ``application.services``
directly — ``from application.services import NLPService``, not ``from
application.services.rag import NLPService``. The subpackages are an internal
filing system, so a service can move between them without touching its callers;
this file is the whole public surface, and it is unchanged from when these were
one flat directory.

    account profiles, and removing one with everything under it
    conversation  sessions and the chats filed under them
    core    settings, logging, and knowing what is already running
    ingest  a document from bytes on disk to chunks in the database
    llm     asking a model something, and insisting on the shape of the answer
    memory  extracting durable facts about a user from a `/memory` message
    rag     answering a question from the notebook's own documents
    studio  generating study material — flashcards, quizzes
"""

from .account import UserService
from .conversation import ConversationService, FetchQueue, MessageService, SourceService
from .core import BaseService, IdempotencyService
from .ingest import (
    AssetIngestService,
    DataService,
    ProcessService,
    TextCorrectionService,
    TextProcessingService,
    UrlSourceService,
)
from .llm import (
    ModelService,
    NvidiaModelService,
    OpenRouterModelService,
    for_source,
    generate_structured,
)
from .memory import MemoryService
from .rag import ChatService, IndexService, NLPService
from .studio import (
    ITEMS_PER_BATCH,
    SUMMARY_BATCH,
    ArtifactService,
    FlashcardService,
    MindMapService,
    QuizService,
    StudioService,
    summarise_chunks,
)

__all__ = [
    "ArtifactService",
    "AssetIngestService",
    "BaseService",
    "ChatService",
    "ConversationService",
    "DataService",
    "FetchQueue",
    "FlashcardService",
    "ITEMS_PER_BATCH",
    "IdempotencyService",
    "IndexService",
    "MemoryService",
    "MessageService",
    "MindMapService",
    "ModelService",
    "NLPService",
    "NvidiaModelService",
    "OpenRouterModelService",
    "ProcessService",
    "SUMMARY_BATCH",
    "TextCorrectionService",
    "QuizService",
    "SourceService",
    "StudioService",
    "TextProcessingService",
    "UserService",
    "UrlSourceService",
    "for_source",
    "generate_structured",
    "summarise_chunks",
]
