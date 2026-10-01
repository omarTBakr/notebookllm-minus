from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from ..project import utcnow


class PersonalUserInfo(BaseModel):
    """One durable fact about a user, extracted from a `/memory` message.

    `key` scopes a fact within a user — "diet", "job" — so a later `/memory`
    call naming the same key replaces the old value instead of piling up a
    second row. `description` is the fact itself, and is also what gets
    embedded: see PostgresVectorRepository, collection `user_memory_<user_id>`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    user_id: str = Field(..., min_length=1, max_length=200)
    key: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
