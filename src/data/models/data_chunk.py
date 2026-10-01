from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from .project import utcnow


class DataChunk(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    # Required: a chunk with no project it belongs to is meaningless. (This was
    # `default=ObjectId`, which defaulted to the *class* rather than an id.)
    project_id: ObjectId = Field(...)
    # Which uploaded asset this chunk came from. chunk_order is a position
    # *within one document*, so without this, two sources in the same project
    # both number their chunks 0..N and a project-wide sort interleaves them.
    # Optional so chunks written before this field existed still load.
    asset_id: Optional[str] = Field(default=None, max_length=200)

    chunk_order: int = Field(..., ge=0, le=1_000_000)
    chunk_content: str = Field(...)
    chunk_metadata: dict = Field(default_factory=dict)

    # A one-line precis of chunk_content, written lazily the first time a Studio
    # feature needs it and then reused for ever.
    #
    # It exists because the whole notebook does not fit in a context window: 694
    # chunks of the Arabic book is ~470k characters, while 694 summaries is
    # ~35k tokens. Flashcards and quizzes are generated from these, never from
    # the raw text -- the chunk itself stays behind as the citation target, so a
    # card can be traced to a page the model never read verbatim.
    #
    # "" means "not summarised yet", which is what makes the generation task
    # resumable: it skips anything already done.
    summary: str = Field(default="")

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
