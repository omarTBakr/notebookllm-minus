from pydantic import BaseModel, Field, field_validator, model_validator

from templates.locales import SUPPORTED_LANGS


class CreateUserRequest(BaseModel):
    """Optional body for POST /chat/users. A blank label is auto-generated."""

    label: str = Field("", max_length=200)


class RenameUserRequest(BaseModel):
    """Body for PATCH /chat/users/{user_id}."""

    label: str = Field(..., min_length=1, max_length=200)

    @field_validator("label")
    @classmethod
    def has_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("label must contain visible characters")
        return value.strip()


class RenameChatRequest(BaseModel):
    """Body for PATCH /chat/chats/{chat_id}."""

    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def has_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must contain visible characters")
        return value.strip()


class RenameAssetRequest(BaseModel):
    """Body for PATCH /chat/chats/{chat_id}/assets/{asset_id}."""

    # 200 is Asset.name's own ceiling — rejecting here rather than letting the
    # model validator do it keeps the failure a 422 about the request body.
    name: str = Field(..., min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def has_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must contain visible characters")
        return value.strip()


class CreateSessionRequest(BaseModel):
    """Body for POST /chat/users/{user_id}/sessions."""

    title: str = Field("New session", min_length=1, max_length=200)


class CreateChatRequest(BaseModel):
    """Body for POST /chat/sessions/{session_id}/chats."""

    title: str = Field("New chat", min_length=1, max_length=200)

    # Which locale's prompts this conversation uses. Validated here so an
    # unknown language is a 422 naming the field, rather than a silent English
    # fallback the user only notices in the model's replies.
    lang: str = Field("en")

    @field_validator("lang")
    @classmethod
    def known_language(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_LANGS:
            raise ValueError(f"lang must be one of {list(SUPPORTED_LANGS)}")
        return normalized


class MessageRequest(BaseModel):
    """Body for POST /chat/chats/{chat_id}/message."""

    # min_length keeps an empty question from being embedded into a
    # meaningless vector that still returns confident-looking neighbours.
    text: str = Field(..., min_length=1)

    # None means "use RETRIEVAL_TOP_K". Bounded because every retrieved chunk
    # goes into the prompt, and the context window is finite.
    top_k: int | None = Field(None, gt=0, le=20)


class SetModelsRequest(BaseModel):
    """Body for PATCH /chat/chats/{chat_id}/models.

    Either field may be omitted to leave that model unchanged. Changing the
    embedding model rebuilds the chat's index, because the vector width is
    fixed when the collection is created.
    """

    generation_model: str | None = Field(None, max_length=200)
    embedding_model: str | None = Field(None, max_length=200)


class ChatSettingsRequest(BaseModel):
    """Body for PATCH /chat/chats/{chat_id}/settings.

    Every field is optional — only what is sent gets written, so the sliders
    can save independently without one control clobbering another. Bounds are
    enforced here so a hand-made request cannot set what the UI cannot.
    """

    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=64, le=32768)

    # Applied to documents attached after the change; existing chunks are left
    # alone, since re-splitting them would orphan their vectors.
    chunk_size: int | None = Field(None, ge=100, le=8000)
    overlap_size: int | None = Field(None, ge=0, le=2000)

    web_search: bool | None = None

    @model_validator(mode="after")
    def overlap_fits_chunk(self):
        # Same rule ProcessRequest enforces: the splitter rejects the
        # combination anyway, and catching it here names the offending fields.
        if self.chunk_size is not None and self.overlap_size is not None:
            if self.overlap_size >= self.chunk_size:
                raise ValueError(
                    f"overlap_size ({self.overlap_size}) must be smaller than "
                    f"chunk_size ({self.chunk_size})"
                )
        return self


class SelectSourcesRequest(BaseModel):
    """Body for PATCH /chat/chats/{chat_id}/sources.

    Carries the sources to switch *off*. Excluding rather than including keeps
    "use everything" as the default, so a source uploaded later is searchable
    immediately instead of silently ignored until someone ticks it.
    """

    excluded_assets: list[str] = Field(default_factory=list)
