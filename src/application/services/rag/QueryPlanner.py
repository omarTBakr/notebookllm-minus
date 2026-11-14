"""Deciding how to answer a question before retrieving anything.

The first step of the agentic answer path. One structured call reads the
question and the last few turns and returns a plan:

* **intent**: what kind of message this is.
    - ``chitchat``: a greeting, thanks, or a question about the assistant
      itself. Nothing to retrieve; answered as plain chat.
    - ``simple``: one thing is being asked. Retrieved and answered in one
      pass, exactly as before (low latency).
    - ``complex``: several things are being asked, or one thing that needs
      separate facts combined. Each sub-query is retrieved and answered on its
      own, then a final call writes the answer from those partial answers and
      the original question.
* **standalone_query**: the question rewritten so it means the same thing
  without the chat history ("and its population?" becomes "What is the
  population of France?"). The reranker scores passages against it.
* **sub_queries**: retrieval queries. For ``simple``, one or two rephrasings
  that retrieve better than the user's own wording. For ``complex``, one
  self-contained query per part.

Every field is required, with no defaults: a defaulted list lets a model that
echoes the schema back parse as a clean, silent empty plan, and
``generate_structured``'s retry-with-the-error loop never fires (the same
reason ``ExtractedFacts.facts`` and ``FlashcardSet.cards`` are required).

*client* must be built with ``thinking=False`` (see ``dependencies.chat_service``):
a reasoning model asked for JSON leaves ``content`` empty and puts everything
in ``reasoning_content``, which fails validation on every retry. That reaches
hosted (OpenAI-compatible) models only: a local Ollama model thinks anyway and
the JSON schema constraint is what keeps its answer parseable (see
GENERATION_THINKING).
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from application.prompts import TemplateParser
from application.providers.chatting import LLMChattingInterface
from shared.enums import ChatRole
from shared.exceptions import StructuredOutputError
from shared.utils import get_logger, get_settings, tracing

from ..llm.structured_generation import generate_structured

logger = get_logger(__name__)

#: Hard ceiling on what the schema accepts. AGENTIC_MAX_SUB_QUERIES (default 3)
#: is the working limit and is applied below; this only bounds the model's own
#: output so a runaway list fails validation instead of fanning out.
SCHEMA_MAX_SUB_QUERIES = 5

#: History turns shown to the planner, and how much of each. Enough to resolve
#: "it" and "the second one"; more only costs tokens.
HISTORY_TURNS = 4
HISTORY_CHARS = 600

Intent = Literal["chitchat", "simple", "complex"]


class QueryPlanSchema(BaseModel):
    """What the planner model must return."""

    intent: Intent
    standalone_query: str = Field(..., min_length=1)
    sub_queries: list[str] = Field(..., min_length=1, max_length=SCHEMA_MAX_SUB_QUERIES)

    model_config = {
        "json_schema_extra": {
            # Deliberately off-topic: a domain example leaks into the answers
            # for every notebook that happens to be about something else.
            "example": {
                "intent": "complex",
                "standalone_query": "EXAMPLE ONLY - How tall is the lighthouse and when was it built?",
                "sub_queries": [
                    "EXAMPLE ONLY - lighthouse height",
                    "EXAMPLE ONLY - lighthouse construction date",
                ],
            }
        }
    }


@dataclass
class QueryPlan:
    """The plan the answer path follows, already cleaned up."""

    intent: Intent
    standalone_query: str
    sub_queries: list[str] = field(default_factory=list)
    #: True when the planner failed and this is the safe default.
    fallback: bool = False

    @property
    def is_complex(self) -> bool:
        return self.intent == "complex" and len(self.sub_queries) > 1


def fallback_plan(question: str) -> QueryPlan:
    """Answer in one grounded pass on the raw question: the old behaviour."""
    return QueryPlan(intent="simple", standalone_query=question, sub_queries=[question], fallback=True)


def format_history(history: list[dict] | None) -> str:
    """The last few turns as plain text for the planner prompt."""
    turns = [m for m in (history or []) if m.get("role") in (ChatRole.USER.value, ChatRole.ASSISTANT.value)]
    lines = []
    for message in turns[-HISTORY_TURNS:]:
        speaker = "User" if message["role"] == ChatRole.USER.value else "Assistant"
        text = " ".join(str(message.get("content") or "").split())
        if len(text) > HISTORY_CHARS:
            text = text[:HISTORY_CHARS] + "…"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines) if lines else "(none)"


# --- chit-chat fast path ----------------------------------------------------------
# Words that, on their own, carry no request for information. An 8B planner
# kept labelling "Hi there, thanks for the help!" as a question to retrieve
# for, so a message made only of these (and punctuation) skips the model call
# altogether. Anything else, including a greeting followed by a real question,
# still goes to the planner.
_SMALL_TALK_WORDS = {
    # en
    "hi",
    "hello",
    "hey",
    "hiya",
    "yo",
    "good",
    "morning",
    "afternoon",
    "evening",
    "night",
    "thanks",
    "thank",
    "you",
    "thx",
    "ty",
    "much",
    "so",
    "very",
    "a",
    "lot",
    "lots",
    "for",
    "the",
    "help",
    "your",
    "great",
    "nice",
    "awesome",
    "perfect",
    "cool",
    "ok",
    "okay",
    "got",
    "it",
    "sure",
    "bye",
    "goodbye",
    "see",
    "later",
    "cheers",
    "there",
    "all",
    "that",
    "was",
    "helpful",
    "job",
    "work",
    "sorry",
    "welcome",
    "appreciate",
    "it's",
    # ar
    "مرحبا",
    "مرحبًا",
    "اهلا",
    "أهلا",
    "أهلًا",
    "السلام",
    "عليكم",
    "وعليكم",
    "صباح",
    "مساء",
    "الخير",
    "النور",
    "شكرا",
    "شكرًا",
    "جزيلا",
    "جزيلًا",
    "جزاك",
    "الله",
    "خيرا",
    "خيرًا",
    "تمام",
    "حسنا",
    "حسنًا",
    "ممتاز",
    "رائع",
    "فهمت",
    "مع",
    "السلامة",
    "وداعا",
    "وداعًا",
    "عفوا",
    "عفوًا",
    "لك",
    "كثيرا",
    "كثيرًا",
    "على",
    "المساعدة",
    "أشكرك",
}
_WORD = re.compile(r"[^\W\d_]+(?:'[a-z]+)?", re.UNICODE)
# Arabic short vowels, tanween and shadda are combining marks: "شكرًا" would
# otherwise split into two "words". Matching ignores them.
_ARABIC_MARKS = re.compile(r"[\u064B-\u065F\u0670]")


def is_small_talk(message: str) -> bool:
    """A short message that asks for nothing: greetings, thanks, goodbyes."""
    text = _ARABIC_MARKS.sub("", (message or "").strip().lower())
    if not text or "?" in text or "؟" in text:
        return False
    words = _WORD.findall(text)
    return 0 < len(words) <= 8 and all(word in _SMALL_TALK_WORDS for word in words)


def normalise(raw: QueryPlanSchema, question: str, max_sub_queries: int) -> QueryPlan:
    """Turn the model's answer into a plan the answer path can trust.

    * blank and duplicate sub-queries are dropped, and the list is cut to
      *max_sub_queries*;
    * ``complex`` with a single sub-query left is just ``simple``: there is
      nothing to combine;
    * ``simple`` with two or more distinct sub-queries is ``complex``: the
      prompt (v2) asks for exactly one query for a simple question, so several
      means the model found separate parts and only mislabelled them;
    * ``chitchat`` keeps no sub-queries, since nothing will be retrieved.
    """
    seen: set[str] = set()
    queries: list[str] = []
    for query in raw.sub_queries:
        cleaned = " ".join(query.split())
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            queries.append(cleaned)
    queries = queries[:max_sub_queries]

    standalone = " ".join(raw.standalone_query.split()) or question

    if raw.intent == "chitchat":
        return QueryPlan(intent="chitchat", standalone_query=standalone, sub_queries=[])

    if not queries:
        queries = [standalone]

    intent: Intent = "complex" if len(queries) > 1 else "simple"

    return QueryPlan(intent=intent, standalone_query=standalone, sub_queries=queries)


# Any letter of the Arabic block. Enough to tell the two languages this app
# speaks apart; a query is "in the question's script" when both have Arabic
# letters or neither does.
_ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u0671-\u06D3]")


def search_queries(queries: list[str], question: str) -> list[str]:
    """*queries*, plus the user's own *question* when the planner translated it.

    The prompt asks for sub-queries "in the user's language", and a small chat
    model ignores that whenever the surrounding text is English (the English
    prompt of an `en` notebook, English turns in the history): an Arabic
    question comes back as English sub-queries (llama3.1:8b, seen live). nomic-embed-text barely matches across
    languages, so an English query ranks the Arabic passages that answer it
    near the bottom of the notebook (53rd to 76th of 77, seen live) and they
    never reach the reranker's pool -- the answer then says the documents do
    not cover a question they answer word for word.

    The translation is kept rather than replaced: it is the right query for a
    question asked in one language about sources in the other. The question
    is added beside it, so passages in the user's own language are searched
    for in that language too.
    """
    script = bool(_ARABIC_LETTER.search(question or ""))
    if not question or any(bool(_ARABIC_LETTER.search(query)) == script for query in queries):
        return queries
    return [*queries, question]


async def plan_query(
    client: LLMChattingInterface,
    question: str,
    lang: str,
    history: list[dict] | None = None,
) -> QueryPlan:
    """The plan for *question*, best-effort.

    Never raises for a model failure: a planner that cannot produce valid JSON
    falls back to one grounded pass on the raw question, which is what this
    application did before it planned at all.
    """
    settings = get_settings()

    if is_small_talk(question):
        logger.info("Planned question (intent=chitchat, fast path, no model call)")
        return QueryPlan(intent="chitchat", standalone_query=question, sub_queries=[])

    parser = TemplateParser(lang=lang, default_lang=settings.DEFAULT_LANG)
    prompt, prompt_ref = parser.get_with_ref(
        "rag",
        "plan_prompt",
        {
            "question": question,
            "history": format_history(history),
            "max_sub_queries": settings.AGENTIC_MAX_SUB_QUERIES,
        },
    )

    with tracing.observation("plan", as_type="chain", input={"question": question}) as span:
        try:
            with tracing.linked_prompt(prompt_ref):
                raw = await generate_structured(client, prompt, QueryPlanSchema)
        except StructuredOutputError as exc:
            logger.warning("Query planning failed, answering the raw question in one pass: %s", exc)
            plan = fallback_plan(question)
        else:
            plan = normalise(raw, question, settings.AGENTIC_MAX_SUB_QUERIES)

        span.update(
            output={
                "intent": plan.intent,
                "standalone_query": plan.standalone_query,
                "sub_queries": plan.sub_queries,
                "fallback": plan.fallback,
            }
        )

    logger.info(
        "Planned question (intent=%s, sub_queries=%d, fallback=%s)",
        plan.intent,
        len(plan.sub_queries),
        plan.fallback,
    )
    return plan
