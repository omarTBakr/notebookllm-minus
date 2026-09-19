from abc import ABC, abstractmethod

from models.db_schema import Artifact


class ArtifactRepository(ABC):
    """Study material generated from a notebook — flashcard decks, quizzes.

    One row per notebook per kind, replaced when it is regenerated. A notebook
    does not accumulate a history of decks: the useful question is "what is the
    current deck for this book", and keeping every past attempt would make the
    UI ask which one the user meant.
    """

    @abstractmethod
    async def create_artifact(self, artifact: Artifact) -> str:
        """Start a set, returning its row id.

        Written before any item exists, with status GENERATING, so the browser
        has something to poll from the moment the task is queued rather than a
        404 for the first few seconds.
        """

    @abstractmethod
    async def find_artifact(self, chat_id: str, kind: str) -> Artifact | None:
        """The current set of this kind for this notebook, or None.

        or-None rather than raising: "this notebook has no quiz yet" is the
        ordinary state of every notebook, not an error worth an exception.
        """

    @abstractmethod
    async def append_items(self, artifact_id: str, items: list[dict]) -> int:
        """Add items to a set that is still generating, returning the new total.

        Append rather than replace, and atomic at the database, because this is
        called once per batch while the user may already be reading. A
        read-modify-write from the worker would lose items if anything else
        touched the row, and would briefly show a shorter deck than the one
        already on screen.
        """

    @abstractmethod
    async def replace_items(self, artifact_id: str, items: list[dict]) -> int:
        """Overwrite a set's items, returning how many it now holds.

        For a final pass that rewrites the whole set once every batch is in --
        a mind map stamping each topic with its branch. Never while batches are
        still appending: that is what append_items is for.
        """

    @abstractmethod
    async def finish_artifact(self, artifact_id: str, status: str, error: str = "") -> None:
        """Mark a set complete or failed.

        A failed set keeps the items it managed to produce: a deck of twelve
        cards that stopped early is worth more than an error page, and the
        status is what lets the UI say which it is.
        """

    @abstractmethod
    async def delete_artifacts_for_chat(self, chat_id: str) -> int:
        """Remove every set for a notebook, returning how many went.

        Called when a notebook is deleted, and when its documents change
        enough that generated material no longer describes them.
        """
