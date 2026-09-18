"""The stored row: whichever items a run produced, and how far it got.

Separate from the model-facing shapes in this package because it answers a
different question. Those describe what a model must return; this describes
what the database holds and what the browser polls — items are appended batch
by batch, so a deck is readable while it is still filling.
"""

from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from enums import ArtifactKind, ArtifactStatus

from ..project import utcnow


class Artifact(BaseModel):
    """A generated set, belonging to one notebook."""

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")

    artifact_id: str = Field(..., max_length=200)

    # The notebook, by its business id -- the same string the URL carries, as
    # AssetRow.project_id holds. Deliberately not the project row's ObjectId
    # that DataChunk uses: this is reached from a chat route, and the identifier
    # mismatch between those two has already cost this project a bug.
    chat_id: str = Field(..., max_length=200)

    kind: ArtifactKind
    status: ArtifactStatus = ArtifactStatus.GENERATING

    # Flashcards or quiz questions, as dicts. Untyped here because one row type
    # holds both, and the kind says which -- the typed shapes above are what
    # validate them on the way in.
    items: list[dict] = Field(default_factory=list)

    # The Celery task that produced this, so a set can be joined to the run that
    # made it and to its progress row.
    source_task_id: str = Field(default="", max_length=200)

    # Why a FAILED set stopped. Kept with whatever items it managed, because a
    # deck of twelve that stopped early is worth more than an error page.
    error: str = Field(default="")

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
