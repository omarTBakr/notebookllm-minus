from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from ..project import utcnow


class User(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    user_id: str = Field(..., min_length=1, max_length=200)

    # Purely cosmetic, shown in the sidebar so two browser profiles are
    # tellable apart. Never used for lookup.
    label: str = Field("", max_length=200)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
