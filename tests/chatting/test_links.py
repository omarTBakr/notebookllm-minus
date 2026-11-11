"""Following figure <-> passage links at answer time."""

import json

from bson import ObjectId

from application.services.rag.ChatService import ChatService
from application.services.rag.links import expand_links
from data.models import ChunkModel, DataChunk
from tests.chatting.test_agentic_rag import FakeNLP, PlannerClient, collect, hit, plan
from tests.chatting.test_multimodal import PIC, image_hit
from tests.support.llm import FakeStreamingChatClient


def partner_of(source_hit, hit_id, caption="A bar chart", kind="image"):
    found = image_hit(hit_id, caption=caption) if kind == "image" else hit(hit_id, text=caption)
    found["score"] = None
    found["linked_from"] = source_hit["id"]
    return found


def service(nlp, vision=False, intent="simple", queries=("revenue chart",)):
    generator = FakeStreamingChatClient(reply="final answer")
    planner = PlannerClient(plan(intent, "revenue chart", *queries))
    return (
        ChatService(generation_client=generator, nlp_controller=nlp, planner_client=planner, vision=vision),
        generator,
    )


# --- expand_links against the fake store ----------------------------------------------------


async def test_expand_links_brings_partners_not_already_among_the_hits(fake_db):
    project = ObjectId()
    await ChunkModel(fake_db).create_chunks(
        [
            DataChunk(
                project_id=project,
                asset_id="a1",
                chunk_order=0,
                chunk_content="As Figure 1 shows, Q4 was strongest.",
                chunk_metadata={"source": "r.pdf", "linked_images": [2]},
            ),
            DataChunk(
                project_id=project,
                asset_id="a1",
                chunk_order=1,
                chunk_content="Figure 1: Revenue by quarter",
                chunk_metadata={"source": "r.pdf", "linked_images": [2]},
            ),
            DataChunk(
                project_id=project,
                asset_id="a1",
                chunk_order=2,
                chunk_content="A bar chart",
                chunk_metadata={"source": "r.pdf", "kind": "image", "linked_chunks": [1, 0]},
            ),
        ]
    )

    passage = hit("0", text="As Figure 1 shows")
    partners = await expand_links(fake_db, [passage])

    assert [p["metadata"]["chunk_order"] for p in partners] == [2]
    assert partners[0]["metadata"]["kind"] == "image"
    assert partners[0]["linked_from"] == passage["id"]
    assert partners[0]["score"] is None

    # From the figure: its passages, minus the one already in the hits.
    figure = image_hit("2")
    partners = await expand_links(fake_db, [figure, passage])
    assert [p["metadata"]["chunk_order"] for p in partners] == [1]

    # Caps are respected.
    assert await expand_links(fake_db, [figure], max_text=0) == []


async def test_expand_links_survives_a_broken_store(fake_db, monkeypatch):
    class Broken:
        def __init__(self, db):
            pass

        async def get_chunks_by_orders(self, *args, **kwargs):
            raise RuntimeError("db down")

    # ChunkModel is a factory function in data.models, so the name links.py
    # bound at import is what to replace, not an attribute on a class.
    monkeypatch.setattr("application.services.rag.links.ChunkModel", Broken)

    assert await expand_links(fake_db, [hit("0")]) == []


# --- ChatService -----------------------------------------------------------------------------------


async def test_a_passage_brings_its_figure_after_the_ranked_hits():
    passage = hit("1", 0.9, text="As Figure 1 shows, Q4 was strongest.")
    chat, generator = service(FakeNLP({"revenue chart": [passage, hit("2", 0.8)]}))

    async def links(hits):
        return [partner_of(passage, "7")]

    events = await collect(chat, question="revenue chart", top_k=2, link_lookup=links)
    citations = next(e for e in events if e["type"] == "meta")["citations"]

    assert [c["chunk_order"] for c in citations] == [1, 2, 7]
    assert citations[2]["kind"] == "image" and citations[2]["linked_to"] == 1
    assert citations[0]["linked_to"] is None
    prompt = generator.calls[-1]["messages"][-1]["content"]
    assert "## Document 3 (belongs with Document 1)" in prompt
    assert "[Image] A bar chart" in prompt


async def test_a_figure_brings_the_passage_that_explains_it():
    figure = image_hit("7")
    chat, generator = service(FakeNLP({"revenue chart": [figure]}))

    async def links(hits):
        return [partner_of(figure, "1", caption="Figure 1: Revenue by quarter", kind="text")]

    events = await collect(chat, question="revenue chart", link_lookup=links)
    citations = next(e for e in events if e["type"] == "meta")["citations"]

    assert [(c["chunk_order"], c["kind"], c["linked_to"]) for c in citations] == [(7, "image", None), (1, "text", 1)]
    assert "## Document 2 (belongs with Document 1)" in generator.calls[-1]["messages"][-1]["content"]


async def test_a_vision_model_sees_the_linked_figure_too():
    passage = hit("1", text="As Figure 1 shows")
    chat, generator = service(FakeNLP({"revenue chart": [passage]}), vision=True)

    async def links(hits):
        return [partner_of(passage, "7")]

    async def pictures(hits):
        return {("a1", 7): PIC}

    await collect(chat, question="revenue chart", link_lookup=links, image_lookup=pictures)

    last = generator.calls[-1]["messages"][-1]
    assert len(last["images"]) == 1
    assert "in order: [2]." in last["content"]


async def test_a_failing_link_lookup_leaves_the_hits_alone():
    chat, _ = service(FakeNLP({"revenue chart": [hit("1")]}))

    async def links(hits):
        raise RuntimeError("db down")

    events = await collect(chat, question="revenue chart", link_lookup=links)

    assert [c["chunk_order"] for c in next(e for e in events if e["type"] == "meta")["citations"]] == [1]
    assert events[-1]["type"] == "done"


async def test_each_part_of_a_complex_question_gets_its_own_partners():
    first = hit("1", text="As Figure 1 shows")
    second = hit("2", text="Table 2 lists the costs")
    nlp = FakeNLP({"revenue": [first], "costs": [second]})
    chat, _ = service(nlp, intent="complex", queries=("revenue", "costs"))

    async def links(hits):
        return [partner_of(hits[0], "7" if hits[0] is first else "8")]

    events = await collect(chat, question="revenue and costs", link_lookup=links)
    citations = next(e for e in events if e["type"] == "meta")["citations"]

    assert sorted(c["chunk_order"] for c in citations) == [1, 2, 7, 8]
    prompts = chat.planner_client.prompts
    part_prompts = [p for p in prompts if "Part:" in p]
    # number_globally numbers the union part by part: [1, 7] then [2, 8] ->
    # documents 1..4, so part 2's partner belongs with Document 3.
    assert any("(belongs with Document 1)" in p for p in part_prompts)
    assert any("(belongs with Document 3)" in p for p in part_prompts)


async def test_with_links_off_nothing_is_added(monkeypatch):
    chat, _ = service(FakeNLP({"revenue chart": [hit("1")]}))
    monkeypatch.setattr(chat.settings, "LINKS_ENABLED", False)
    called = []

    async def links(hits):
        called.append(1)
        return [partner_of(hits[0], "7")]

    events = await collect(chat, question="revenue chart", link_lookup=links)

    assert not called
    assert [c["chunk_order"] for c in next(e for e in events if e["type"] == "meta")["citations"]] == [1]


def test_plan_fixture_is_json_serialisable():
    assert json.loads(json.dumps(plan("simple", "q", "q")))["intent"] == "simple"
