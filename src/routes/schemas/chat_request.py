from pydantic import BaseModel, Field, field_validator

from templates.locales import SUPPORTED_LANGS


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
