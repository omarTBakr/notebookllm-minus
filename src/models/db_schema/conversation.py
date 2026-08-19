"""Documents backing the chat feature: users, sessions, chats, messages.

Grouped in one module because they are one feature and always change together
— a field added to Chat is usually a field the Message writer cares about too.

Identity here is deliberately thin: a User is an opaque uuid with no password,
no email and no verification. "New user" mints one, "current user" is whatever
id the browser kept. It exists to scope conversations, not to prove anything.
"""

from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

from enums import ChatRole

from .project import utcnow


class User(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    user_id: str = Field(..., min_length=1, max_length=200)

    # Purely cosmetic, shown in the sidebar so two browser profiles are
    # tellable apart. Never used for lookup.
    label: str = Field("", max_length=200)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Session(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    session_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=200)

    title: str = Field("New session", min_length=1, max_length=200)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Chat(BaseModel):
    """One conversation, and one document space.

    ``chat_id`` doubles as the ``project_id`` used by /data, /process and /nlp.
    There is no second field holding the same value: Project.project_id already
    carries a unique index, and a copy would only be something to keep in sync.

    The title lives here rather than on Project.name because /data and /process
    overwrite Project.name with the uploaded filename on every call.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    chat_id: str = Field(..., min_length=1, max_length=200)
    session_id: str = Field(..., min_length=1, max_length=200)
    user_id: str = Field(..., min_length=1, max_length=200)

    title: str = Field("New chat", min_length=1, max_length=200)

    # Which locale's prompts to use. Validated against SUPPORTED_LANGS at the
    # route boundary; stored loosely so adding a language never invalidates
    # rows already written.
    lang: str = Field("en", min_length=2, max_length=8)

    # Per-chat model overrides. None means "whatever .env says", so existing
    # chats keep working and the default stays in one place.
    generation_model: Optional[str] = Field(default=None, max_length=200)

    # The embedding model and the width it produces travel together: the width
    # is baked into the vector collection, so storing one without the other
    # would leave no way to tell whether an index matches its model.
    embedding_model: Optional[str] = Field(default=None, max_length=200)
    embedding_dimensions: Optional[int] = Field(default=None, gt=0)

    # A cheap hint for list views (the 📎 badge). Authoritative groundedness is
    # read from the vector index at answer time, so a half-finished upload
    # cannot make a chat claim documents it has no vectors for.
    has_documents: bool = False

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("title")
    @classmethod
    def title_has_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must contain visible characters")
        return value.strip()


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
