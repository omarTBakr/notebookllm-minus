"""Study material generated from a notebook's documents.

Content, like the uploads next to it in this package, but produced rather than
received — a flashcard deck is as much "what this notebook holds" as the PDF it
came from.
"""

from enum import StrEnum


class ArtifactKind(StrEnum):
    """Which Studio feature produced a set.

    The value is what a URL carries (`/studio/flashcards`) and what a row is
    keyed by, so these strings are part of the API and cannot be renamed
    casually.

    Only the two that are built. The Studio panel shows nine tiles, but a
    member here means "there is a generator behind this", and adding one before
    that is true would let a route accept a kind nothing can produce.
    """

    FLASHCARDS = "flashcards"
    QUIZ = "quiz"


class ArtifactStatus(StrEnum):
    """How far along a set is.

    GENERATING is a readable state, not a placeholder: items are appended as
    each batch finishes, so a set can be displayed and used while it is still
    filling. That is the whole point of the design — waiting for 694 chunks to
    be summarised before showing anything would be minutes of blank screen.

    FAILED keeps whatever was produced before the failure. A deck of twelve
    cards that stopped early is worth more than an error page, and the status
    is what lets the UI say so rather than implying the set is finished.
    """

    GENERATING = "generating"
    COMPLETE = "complete"
    FAILED = "failed"
