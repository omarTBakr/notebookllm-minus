"""The agentic answer path: plan, then chitchat / simple / complex."""

import json
import re

from application.services.rag.ChatService import ChatService
from application.services.rag.QueryPlanner import (
    QueryPlanSchema,
    format_history,
    normalise,
    plan_query,
)
from tests.support.llm import FakeChatClient, FakeStreamingChatClient


def hit(hit_id, score=0.5, text=None, source="doc.pdf"):
    return {
        "id": hit_id,
        "score": score,
        "text": text or f"passage {hit_id}",
        "metadata": {"asset_id": "a1", "chunk_order": int(hit_id) if hit_id.isdigit() else 0, "source": source},
    }


class FakeNLP:
    def __init__(self, by_query, points=5):
        self.by_query = by_query
        self.points = points
        self.searched = []

    async def get_index_info(self, chat_id):
        return {"exists": True, "info": {"points_count": self.points}}

    async def search(self, chat_id, query, limit, asset_ids=None):
        self.searched.append(query)
        return self.by_query.get(query, [])[:limit]


class PlannerClient(FakeChatClient):
    """Answers the planner with *plan* and each sub-answer by echoing its part."""

    def __init__(self, plan, fail_parts=()):
        super().__init__()
        self.plan = plan
        self.fail_parts = set(fail_parts)
        self.prompts = []

    async def _generate_text(self, messages, max_tokens, temperature):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        part = re.search(r"Part: (.*)\n", prompt)
        if part:
            if part.group(1) in self.fail_parts:
                raise RuntimeError("model down")
            numbers = re.findall(r"## Document (\d+)", prompt)
            return f"finding for {part.group(1)} " + "".join(f"[{n}]" for n in numbers)
        return json.dumps(self.plan)


def plan(intent, standalone, *queries):
    return {"intent": intent, "standalone_query": standalone, "sub_queries": list(queries)}


async def collect(service, question="the question", **kwargs):
    events = []
    async for event in service.answer_stream(chat_id="c", question=question, lang="en", **kwargs):
        events.append(event)
    return events


def make(nlp, planner, answer="final answer"):
    generator = FakeStreamingChatClient(reply=answer)
    return ChatService(generation_client=generator, nlp_controller=nlp, planner_client=planner), generator


# --- planner unit -----------------------------------------------------------------


def test_normalise_drops_duplicates_and_caps():
    raw = QueryPlanSchema(intent="complex", standalone_query="  q  ", sub_queries=["a", " A ", "b", "c", "d"])
    result = normalise(raw, "q", max_sub_queries=3)
    assert result.sub_queries == ["a", "b", "c"]
    assert result.standalone_query == "q"
    assert result.is_complex


def test_complex_with_one_part_left_is_simple():
    raw = QueryPlanSchema(intent="complex", standalone_query="q", sub_queries=["a", "a"])
    assert normalise(raw, "q", 3).intent == "simple"


def test_chitchat_keeps_no_queries():
    raw = QueryPlanSchema(intent="chitchat", standalone_query="hello", sub_queries=["hello"])
    result = normalise(raw, "hello", 3)
    assert result.intent == "chitchat" and result.sub_queries == []


def test_history_is_trimmed_to_recent_turns():
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)] + [{"role": "system", "content": "x"}]
    text = format_history(history)
    assert text.splitlines() == ["User: q6", "User: q7", "User: q8", "User: q9"]
    assert format_history([]) == "(none)"


async def test_planner_falls_back_on_unparseable_output():
    result = await plan_query(FakeChatClient(reply="not json at all"), "raw question", "en")
    assert result.fallback and result.intent == "simple"
    assert result.sub_queries == ["raw question"]


async def test_planner_sees_history_and_returns_the_plan():
    client = PlannerClient(plan("simple", "What is the capital of France?", "capital of France"))
    result = await plan_query(
        client, "and its capital?", "en", history=[{"role": "user", "content": "Tell me about France"}]
    )
    assert result.standalone_query == "What is the capital of France?"
    assert "User: Tell me about France" in client.prompts[0]
    assert "and its capital?" in client.prompts[0]


def test_global_numbering_dedupes_across_parts():
    union, numbers = ChatService.number_globally([[hit("1"), hit("2")], [hit("2"), hit("3")], []])
    assert [h["id"] for h in union] == ["1", "2", "3"]
    assert numbers == [[1, 2], [2, 3], []]


# --- answer_stream ----------------------------------------------------------------


async def test_chitchat_skips_retrieval():
    nlp = FakeNLP({})
    service, generator = make(nlp, PlannerClient(plan("chitchat", "hi there", "hi there")))

    events = await collect(service, question="hi there")

    assert nlp.searched == []
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["grounded"] is False
    assert not any(e["type"] == "step" for e in events)
    # The plain chat prompt, not the rag one.
    assert "Document" not in generator.calls[-1]["messages"][-1]["content"]


async def test_simple_retrieves_on_the_planned_queries():
    nlp = FakeNLP({"rephrased query": [hit("1"), hit("2")]})
    service, generator = make(nlp, PlannerClient(plan("simple", "standalone q", "rephrased query")))

    events = await collect(service, question="original q")

    assert nlp.searched == ["rephrased query"]
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["grounded"] and [c["num"] for c in meta["citations"]] == [1, 2]
    assert not any(e["type"] == "step" for e in events)
    # The user's own wording goes to the answer model.
    assert "original q" in generator.calls[-1]["messages"][-1]["content"]
    assert events[-1] == {"type": "done"}


async def test_complex_answers_each_part_then_synthesises():
    nlp = FakeNLP({"part a": [hit("1"), hit("2")], "part b": [hit("2"), hit("3")]})
    planner = PlannerClient(plan("complex", "a and b?", "part a", "part b"))
    service, generator = make(nlp, planner, answer="combined [1] [3]")

    events = await collect(service, question="a and b?")
    kinds = [e["type"] for e in events]

    # plan step, then meta with every passage numbered once, then part steps,
    # then synthesis, then the streamed answer.
    assert kinds[0] == "step" and events[0]["phase"] == "plan"
    meta = events[kinds.index("meta")]
    assert [c["num"] for c in meta["citations"]] == [1, 2, 3]
    done_parts = sorted(e["index"] for e in events if e.get("phase") == "part" and e["status"] == "done")
    assert done_parts == [0, 1]
    assert kinds.index("meta") < kinds.index("delta")

    # Each sub-answer saw only its own passages, under their global numbers.
    sub_prompts = [p for p in planner.prompts if "Part:" in p]
    part_b = next(p for p in sub_prompts if "Part: part b" in p)
    assert "## Document 2" in part_b and "## Document 3" in part_b and "## Document 1" not in part_b

    # The synthesis prompt carries both findings and the original question,
    # never the raw passages.
    synthesis = generator.calls[-1]["messages"][-1]["content"]
    assert "finding for part a [1][2]" in synthesis
    assert "finding for part b [2][3]" in synthesis
    assert "a and b?" in synthesis
    assert "passage 1" not in synthesis

    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    assert answer.strip() == "combined [1] [3]"


async def test_a_failed_or_empty_part_is_reported_not_fatal():
    nlp = FakeNLP({"part a": [hit("1")], "part b": [hit("2")], "part c": []})
    planner = PlannerClient(plan("complex", "q", "part a", "part b", "part c"), fail_parts={"part b"})
    service, generator = make(nlp, planner)

    events = await collect(service)

    synthesis = generator.calls[-1]["messages"][-1]["content"]
    assert "finding for part a" in synthesis
    assert synthesis.count("The documents do not cover this part.") == 2
    assert events[-1] == {"type": "done"}


async def test_complex_with_nothing_found_answers_ungrounded():
    nlp = FakeNLP({})
    service, generator = make(nlp, PlannerClient(plan("complex", "q", "x", "y")))

    events = await collect(service)

    meta = next(e for e in events if e["type"] == "meta")
    assert meta["grounded"] is False
    assert not any(e.get("phase") == "synthesis" for e in events)
    assert events[-1] == {"type": "done"}


async def test_agentic_off_makes_no_planning_call(monkeypatch):
    monkeypatch.setenv("AGENTIC_RAG_ENABLED", "false")
    nlp = FakeNLP({"raw q": [hit("1")]})
    planner = PlannerClient(plan("complex", "x", "a", "b"))
    service, _ = make(nlp, planner)

    await collect(service, question="raw q")

    assert planner.prompts == []
    assert nlp.searched == ["raw q"]


async def test_ungrounded_chat_is_never_planned():
    nlp = FakeNLP({}, points=0)
    planner = PlannerClient(plan("complex", "x", "a", "b"))
    service, _ = make(nlp, planner)

    await collect(service)

    assert planner.prompts == []
