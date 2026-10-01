"""Flashcards: one fact per card, front and back, tied to the page it came from."""

from data.models import FlashcardSet
from shared.enums import ArtifactKind

from .ArtifactService import ArtifactService


class FlashcardService(ArtifactService):
    schema = FlashcardSet
    prompt_key = "flashcards_prompt"
    field = "cards"
    kind = ArtifactKind.FLASHCARDS
