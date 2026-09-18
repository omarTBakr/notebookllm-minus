"""Study material generated from a notebook, and the shapes a model must return.

Two kinds of thing live here, and the split is why this is a package.

`Flashcard`, `QuizQuestion` and `ChunkSummary` are the *contract with the
model*: each is handed to `generate_structured` as a JSON Schema and validates
what comes back, and their field names appear in prompts, so renaming one
changes what the model is asked for. Each sits with the set it arrives in.

`Artifact` in record.py is the *row* — whichever of those a run produced, plus
the state that lets a half-finished set be displayed.

Every item carries `chunk_order`, which is what keeps a generated card
falsifiable. The model never sees a chunk's text, only its one-line summary, so
without the chunk reference there would be no way to check a claim against the
document or to open the page it came from.
"""

from .flashcard import Flashcard, FlashcardSet
from .quiz import QuizQuestion, QuizSet
from .record import Artifact
from .summary import ChunkSummary, SummarySet

__all__ = [
    "Artifact",
    "ChunkSummary",
    "Flashcard",
    "FlashcardSet",
    "QuizQuestion",
    "QuizSet",
    "SummarySet",
]
