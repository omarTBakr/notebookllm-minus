from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..project import utcnow


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

    # Generation knobs, per chat. None means "use the .env default", so a chat
    # that was never tuned follows the global setting rather than freezing a
    # copy of whatever it happened to be at creation time.
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=64, le=32768)

    # Splitter settings, applied to documents attached *after* they change.
    # Existing chunks are not re-split — that would orphan their vectors.
    chunk_size: Optional[int] = Field(default=None, ge=100, le=8000)
    overlap_size: Optional[int] = Field(default=None, ge=0, le=2000)

    # Ground answers in web search results as well as uploaded documents.
    # Stored and surfaced now; no retrieval backend behind it yet.
    web_search: bool = False

    # The color a citation's cited passage is highlighted in, in this
    # notebook. A real default rather than None: unlike temperature or
    # max_tokens there is no .env-wide fallback to inherit, so every chat
    # needs one from the moment it is created.
    highlight_color: str = Field("#FFFF00", pattern=r"^#[0-9A-Fa-f]{6}$")

    # Sources switched *off* for this notebook, by asset_id.
    #
    # Excluded rather than included on purpose: the default is empty, which
    # means "use everything", so a newly uploaded source is searchable at once
    # instead of being invisible until someone remembers to tick it.
    excluded_assets: list[str] = Field(default_factory=list)

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
