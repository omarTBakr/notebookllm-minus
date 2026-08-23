from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from enums import ChatRole

from ..project import utcnow


class Message(BaseModel):
    """One turn. Serialises straight into the provider-neutral chat format."""

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    message_id: str = Field(..., min_length=1, max_length=200)
    chat_id: str = Field(..., min_length=1, max_length=200)

    # Reuses the enum the chat providers already speak, so history needs no
    # translation on its way into generate_text/stream_text.
    role: ChatRole = Field(...)
    content: str = Field(...)

    # Only ever populated on assistant turns, and only when the answer was
    # grounded: [{source, asset_id, chunk_order, score}, ...].
    citations: list[dict] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=utcnow)
