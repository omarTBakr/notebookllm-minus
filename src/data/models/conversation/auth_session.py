from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from ..project import utcnow


class AuthSession(BaseModel):
    """One signed-in browser.

    Not to be confused with ``Session``, which groups a user's notebooks. The
    browser holds a random token in an HttpOnly cookie; only its sha256 is
    stored here, so a leaked table cannot be replayed as cookies.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    token_hash: str = Field(..., min_length=64, max_length=64)
    user_id: str = Field(..., min_length=1, max_length=200)

    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=utcnow)
