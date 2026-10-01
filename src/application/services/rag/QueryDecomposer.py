"""Splitting one question into the retrieval queries that answer it.

A single embed-and-search call already does badly on a compound question --
"what's the capital of France and what's its population" retrieves whichever
half of the question the embedding leans towards, and the other half comes
back ungrounded. This applies the same one-shot "prompt a model, validate the
JSON" pattern `MemoryService.extract_and_store` uses (see
`application/services/llm/structured_generation.py`) to turn one question into 1-3
retrieval-oriented queries: a real split when the question is compound, or
just a rephrasing when it isn't. `ChatService` runs one search per query,
concurrently, and merges the hits -- the model is never shown the
sub-questions, only ever the original one.

`queries` below is required with `min_length=1`, for the same reason
`ExtractedFacts.facts` and `FlashcardSet.cards` are (see those modules): a
defaulted list lets a model that echoes the schema back instead of filling it
in parse as a clean, silent empty list, and `generate_structured`'s
retry-with-the-error-attached loop never fires. Unlike those two, an empty
answer here is never a legitimate one -- there is always at least the
original question to fall back to -- so the floor is 1, not 0.
"""

from pydantic import BaseModel, Field

from application.prompts import TemplateParser
from application.providers.chatting import LLMChattingInterface
from shared.exceptions import StructuredOutputError
from shared.utils import get_logger, get_settings

from ..llm.structured_generation import generate_structured

logger = get_logger(__name__)

#: Confirmed with the user: not 5, not unbounded. Bounds both the model's own
#: output and the number of concurrent search() calls answer_stream fans out
#: to, so a heavily compound question cannot turn into an unbounded retrieval
#: burst.
MAX_SUB_QUESTIONS = 3


class SubQuestions(BaseModel):
    """1-3 retrieval-oriented queries decomposed from one question."""

    queries: list[str] = Field(..., min_length=1, max_length=MAX_SUB_QUESTIONS)

    model_config = {
        "json_schema_extra": {
            "example": {
                "queries": [
                    "EXAMPLE ONLY - first retrieval-oriented query",
                    "EXAMPLE ONLY - second query, only if the question is genuinely compound",
                ]
            }
        }
    }


async def decompose_query(client: LLMChattingInterface, question: str, lang: str) -> list[str]:
    """1-3 retrieval-oriented queries for *question*, best-effort.

    Falls back to ``[question]`` -- exactly today's single search, unchanged
    -- when `generate_structured` raises `StructuredOutputError` after its own
    retries. A decomposition failure must never surface as a user-facing
    error or block the answer; retrieval with the raw question is still
    perfectly possible.

    *client* must be built with ``thinking=False`` (see
    `presentation/dependencies.py`'s `chat_service`): a reasoning model asked
    for structured output puts everything in `reasoning_content` and leaves
    `content` empty, which `generate_structured` sees as an empty string to
    parse and fails validation on every retry -- the same requirement
    `MemoryService`'s own `chat_client` has, for the same reason.
    """
    parser = TemplateParser(lang=lang, default_lang=get_settings().DEFAULT_LANG)
    prompt = parser.get("rag", "decompose_prompt", {"question": question})

    try:
        result = await generate_structured(client, prompt, SubQuestions)
    except StructuredOutputError as exc:
        logger.warning("Query decomposition failed, falling back to the raw question: %s", exc)
        return [question]

    # Belt and suspenders: max_length on the schema already rejects a longer
    # list at validation, but a slice here costs nothing and keeps the
    # fan-out's own bound from depending solely on the model's cooperation.
    return result.queries[:MAX_SUB_QUESTIONS]
