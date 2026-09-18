"""Flashcards: one fact per card, front and back, tied to the page it came from."""

from enums import ArtifactKind
from models.db_schema import FlashcardSet

from .ArtifactController import ArtifactController


class FlashcardController(ArtifactController):
    schema = FlashcardSet
    prompt_key = "flashcards_prompt"
    field = "cards"
    kind = ArtifactKind.FLASHCARDS
