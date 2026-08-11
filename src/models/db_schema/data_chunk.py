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

    chunk_order: int = Field(..., ge=0, le=1_000_000)
    chunk_content: str = Field(...)
    chunk_metadata: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
