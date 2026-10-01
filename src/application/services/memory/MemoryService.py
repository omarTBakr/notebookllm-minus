"""Turning a `/memory` message into stored, embedded facts.

There is no agent/tool-calling framework in this codebase, and this does not
need one: extraction is the same one-shot "prompt a model, validate the JSON"
problem every Studio tile already solves with `generate_structured` (see
`application/services/llm/structured_generation.py`). This reuses it rather than adding
a second way to ask a model for a shape.

`facts` below is a required field for the same reason `FlashcardSet.cards` is
(see `data/models/artifact/flashcard.py`): with a default, any JSON object
-- including one where the model echoed the schema back instead of filling it
in -- parses as a clean, silent zero facts, and `generate_structured`'s repair
loop never fires. Required means an empty list is still a valid, deliberate
"nothing worth remembering here" answer, but a malformed one is a
ValidationError that gets retried with the error attached.
"""

import re

from pydantic import BaseModel, Field

from application.prompts import TemplateParser
from application.providers.chatting import LLMChattingInterface
from application.providers.embedding import LLMEmbeddingInterface
from data.repositories.interfaces import PersonalUserInfoRepository, VectorRepository
from shared.enums import EmbeddingInputType

from ..core.BaseService import BaseService
from ..llm.structured_generation import generate_structured

# Collection names are far more restricted than free-form user_id input; see
# NLPService.collection_name for the same rule applied to per-project
# collections.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")

#: Results returned by search() — the composer's memory button only, not the
#: automatic per-question fold (see list_facts, which returns everything).
MEMORY_TOP_K = 5


class ExtractedFact(BaseModel):
    """One fact as the model returned it."""

    key: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)


class ExtractedFacts(BaseModel):
    """What one `/memory` message is expected to yield."""

    facts: list[ExtractedFact] = Field(...)

    model_config = {
        "json_schema_extra": {
            "example": {
                "facts": [
                    {
                        "key": "EXAMPLE ONLY - short_snake_case_label",
                        "description": "EXAMPLE ONLY - one self-contained sentence stating the fact",
                    }
                ]
            }
        }
    }


class MemoryService(BaseService):
    """A `/memory` message in, durable facts (stored and embedded) out."""

    def __init__(
        self,
        embedding_client: LLMEmbeddingInterface,
        vector_repo: VectorRepository,
        personal_info_repo: PersonalUserInfoRepository,
        chat_client: LLMChattingInterface | None = None,
    ) -> None:
        # chat_client is optional: only extract_and_store needs a model —
        # list_facts (a plain read) and search (an embed + vector lookup)
        # never generate text, so a caller that only reads never has to build
        # a chat client just to satisfy this constructor.
        super().__init__()

        self.chat_client = chat_client
        self.embedding_client = embedding_client
        self.vector_repo = vector_repo
        self.personal_info_repo = personal_info_repo

    @staticmethod
    def collection_name(user_id: str) -> str:
        """The vector collection holding one user's fact embeddings.

        One collection per user, not one shared table filtered by metadata:
        PostgresVectorRepository already models a "collection" as one table per
        caller (per chat, per project), so this follows the same shape rather
        than teaching search_by_vector a second kind of filter.
        """
        return f"user_memory_{_UNSAFE_NAME_CHARS.sub('_', str(user_id))}"

    async def extract_and_store(self, user_id: str, text: str, lang: str) -> list[ExtractedFact]:
        """Extract facts from *text* and upsert + (re-)embed each one.

        Raises `StructuredOutputError` (via `generate_structured`) if the model
        never returns anything parseable after its retries — the caller decides
        how to surface that to the chat.
        """
        parser = TemplateParser(lang=lang, default_lang=self.settings.DEFAULT_LANG)
        prompt = parser.get("memory", "extract_prompt", {"text": text})

        result = await generate_structured(self.chat_client, prompt, ExtractedFacts)

        if not result.facts:
            return []

        # upsert_fact returns the row id, not the row -- callers here already
        # have `key`/`description` from the model's own answer, so re-reading
        # them back would just be a second round trip for nothing. It matches
        # the row id across calls to the same (user_id, key), which is what
        # makes the insert_many below an embedding *update* rather than a new,
        # orphaned vector on every repeat `/memory` naming the same key.
        record_ids = [
            await self.personal_info_repo.upsert_fact(user_id, fact.key, fact.description) for fact in result.facts
        ]

        await self._embed(user_id, result.facts, record_ids)

        return result.facts

    async def _embed(self, user_id: str, facts: list[ExtractedFact], record_ids: list[str]) -> None:
        collection = self.collection_name(user_id)

        await self.vector_repo.create_collection(collection, self.embedding_client.embedding_size)

        vectors = await self.embedding_client.embed([fact.description for fact in facts], EmbeddingInputType.DOCUMENT)

        await self.vector_repo.insert_many(
            collection,
            texts=[fact.description for fact in facts],
            vectors=vectors,
            metadata=[{"user_id": user_id, "key": fact.key} for fact in facts],
            record_ids=record_ids,
        )

    # --- reading ---------------------------------------------------------------

    async def search(self, user_id: str, question: str, limit: int = MEMORY_TOP_K) -> list[dict]:
        """The *limit* facts closest to *question*, best first.

        Unlike NLPService.search, a missing collection is not an error --
        "this user has never used /memory" is the ordinary case, not a
        caller mistake to report. Both ChatService.answer_stream (folding
        the closest facts into every answer, and showing which ones) and the
        composer's memory button call this.
        """
        collection = self.collection_name(user_id)

        if not await self.vector_repo.collection_exists(collection):
            return []

        # QUERY, not DOCUMENT: asymmetric models embed the two differently,
        # same reasoning as NLPService.search.
        vectors = await self.embedding_client.embed([question], EmbeddingInputType.QUERY)

        hits = await self.vector_repo.search_by_vector(
            collection_name=collection,
            vector=vectors[0],
            limit=limit,
        )

        # Same floor NLPService.search applies to document hits: without
        # one, "top k" always returns k once the user has that many facts,
        # however unrelated to the question they are.
        floor = self.settings.RETRIEVAL_MIN_SCORE
        if floor:
            hits = [h for h in hits if h.get("score") is not None and h["score"] >= floor]

        return hits

    @staticmethod
    def to_citations(hits: list[dict]) -> list[dict]:
        """A search() hit, shaped for the composer's memory button.

        No asset_id/page/chunk_order the way ChatService.to_citations has
        -- a memory fact has no page to link back to, just a key and the
        description that was embedded.
        """
        citations = []

        for number, hit in enumerate(hits, start=1):
            metadata = hit.get("metadata") or {}
            citations.append(
                {
                    "num": number,
                    "key": metadata.get("key"),
                    "description": hit.get("text") or "",
                    "score": (round(hit["score"], 4) if hit.get("score") is not None else None),
                }
            )

        return citations
