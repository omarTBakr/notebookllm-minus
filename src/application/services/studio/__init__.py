"""Generating study material from a notebook: the Studio panel's tiles.

`ArtifactService` holds what every generated kind shares — numbering
summaries within a batch, calling the model, translating what comes back into
citations that point at real chunks, and dropping what cannot be placed. Each
kind is a subclass declaring the three things that actually differ: the schema
to fill, the prompt to ask with, and the field the items arrive under.

A new generated tile belongs here as another subclass, not as another branch
in the task that drives the batching.
"""

from .ArtifactService import ITEMS_PER_BATCH, ArtifactService
from .FlashcardService import FlashcardService
from .MindMapService import MindMapService
from .QuizService import QuizService
from .StudioService import StartedGeneration, StudioService
from .SummaryService import SUMMARY_BATCH, summarise_chunks

__all__ = [
    "ITEMS_PER_BATCH",
    "ArtifactService",
    "FlashcardService",
    "MindMapService",
    "QuizService",
    "StartedGeneration",
    "StudioService",
    "SUMMARY_BATCH",
    "summarise_chunks",
]
