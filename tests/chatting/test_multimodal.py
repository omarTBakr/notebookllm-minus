"""Pictures on the answering path: wire formats, retrieval, and the vision gate."""

import base64

from application.providers.chatting.LLMChattingInterface import (
    LLMChattingInterface as I,
)
from application.services.rag.ChatService import ChatService
from tests.chatting.test_agentic_rag import FakeNLP, PlannerClient, collect, hit, plan
from tests.support.llm import FakeStreamingChatClient

PIC = (b"\x89PNG\r\n\x1a\nfake", "image/png")
B64 = base64.b64encode(PIC[0]).decode()


# --- neutral message and wire formats ----------------------------------------------


def test_construct_message_keeps_content_a_string_and_carries_pictures_beside_it():
    message = I.construct_message("user", "look", [PIC])

    assert message["content"] == "look"
    assert message["images"] == [{"mime": "image/png", "data": B64}]


def test_a_message_without_pictures_has_no_images_key():
    assert "images" not in I.construct_message("user", "plain")


def test_alternate_merges_pictures_of_two_consecutive_user_turns():
    merged = I._alternate([I.construct_message("user", "one", [PIC]), I.construct_message("user", "two", [PIC])])

    assert len(merged) == 1
    assert merged[0]["content"] == "one\n\ntwo"
    assert len(merged[0]["images"]) == 2


def test_ollama_shape_is_a_list_of_base64_strings():
    (out,) = I._ollama_messages([I.construct_message("user", "q", [PIC])])

    assert out["images"] == [B64] and out["content"] == "q"


def test_openai_shape_is_content_parts_with_a_data_url():
    (out,) = I._openai_messages([I.construct_message("user", "q", [PIC])])

    assert out["content"][0] == {"type": "text", "text": "q"}
    assert out["content"][1]["image_url"]["url"] == f"data:image/png;base64,{B64}"


def test_anthropic_shape_puts_the_image_block_before_the_text():
    (out,) = I._anthropic_messages([I.construct_message("user", "q", [PIC])])

    assert out["content"][0]["type"] == "image"
    assert out["content"][0]["source"] == {"type": "base64", "media_type": "image/png", "data": B64}
    assert out["content"][1] == {"type": "text", "text": "q"}


def test_plain_messages_pass_through_every_shape_unchanged():
    messages = [I.construct_message("system", "s"), I.construct_message("user", "q")]

    assert I._ollama_messages(messages) == messages
    assert I._openai_messages(messages) == messages
    assert I._anthropic_messages(messages) == messages


def test_trace_input_never_carries_the_base64():
    traced = I._for_trace([I.construct_message("user", "q", [PIC])])

    assert B64 not in str(traced)
    assert "image/png" in traced[0]["images"][0]


async def test_generate_text_hands_the_picture_to_the_provider():
    client = FakeStreamingChatClient()

    await client.generate_text("describe", images=[PIC])

    assert client.calls[-1]["messages"][-1]["images"][0]["data"] == B64


# --- retrieval and answering ----------------------------------------------------------


def image_hit(hit_id, score=0.6, caption="A bar chart of revenue"):
    found = hit(hit_id, score=score, text=caption)
    found["metadata"]["kind"] = "image"
    return found


class FakeImageIndex:
    def __init__(self, by_text=None, by_image=None, fail=False):
        self.by_text = by_text or {}
        self.by_image = by_image or []
        self.fail = fail
        self.calls = []

    async def search(self, chat_id, *, text=None, image=None, limit=None, asset_ids=None):
        self.calls.append({"text": text, "image": image})
        if self.fail:
            raise RuntimeError("embedder down")
        return list(self.by_image) if image is not None else list(self.by_text.get(text, []))


def service(nlp, images, vision=False, answer="final answer"):
    generator = FakeStreamingChatClient(reply=answer)
    planner = PlannerClient(plan("simple", "revenue chart", "revenue chart"))
    return (
        ChatService(
            generation_client=generator,
            nlp_controller=nlp,
            planner_client=planner,
            image_index=images,
            vision=vision,
        ),
        generator,
    )


async def test_without_a_reranker_pictures_follow_the_passages():
    nlp = FakeNLP({"revenue chart": [hit("1", 0.9), hit("2", 0.8)]})
    images = FakeImageIndex(by_text={"revenue chart": [image_hit("7")]})
    chat, _ = service(nlp, images)

    events = await collect(chat, question="revenue chart", top_k=1)
    citations = next(e for e in events if e["type"] == "meta")["citations"]

    assert [c["chunk_order"] for c in citations] == [1, 7]
    assert citations[1]["kind"] == "image" and citations[0]["kind"] == "text"


async def test_image_hits_are_labelled_as_images_in_the_prompt():
    nlp = FakeNLP({"revenue chart": []})
    images = FakeImageIndex(by_text={"revenue chart": [image_hit("7")]})
    chat, generator = service(nlp, images)

    await collect(chat, question="revenue chart")

    prompt = generator.calls[-1]["messages"][-1]["content"]
    assert "[Image] A bar chart of revenue" in prompt


async def test_a_vision_model_gets_the_cited_pictures_and_which_document_each_is():
    nlp = FakeNLP({"revenue chart": [hit("1", 0.9)]})
    images = FakeImageIndex(by_text={"revenue chart": [image_hit("7")]})
    chat, generator = service(nlp, images, vision=True)

    async def lookup(hits):
        return {("a1", 7): PIC}

    await collect(chat, question="revenue chart", image_lookup=lookup)

    last = generator.calls[-1]["messages"][-1]
    assert len(last["images"]) == 1
    assert "The images attached to this message are, in order: [2]." in last["content"]


async def test_a_model_without_vision_gets_no_pictures():
    nlp = FakeNLP({"revenue chart": []})
    images = FakeImageIndex(by_text={"revenue chart": [image_hit("7")]})
    chat, generator = service(nlp, images, vision=False)

    async def lookup(hits):
        raise AssertionError("pictures must not be loaded for a model without vision")

    await collect(chat, question="revenue chart", image_lookup=lookup)

    assert "images" not in generator.calls[-1]["messages"][-1]


async def test_an_attached_picture_is_searched_and_its_caption_joins_the_query():
    nlp = FakeNLP({"revenue chart": []})
    images = FakeImageIndex(by_image=[image_hit("9", caption="the same chart")])
    chat, generator = service(nlp, images, vision=True)

    events = await collect(chat, question="where is this?", image=PIC, image_caption="A bar chart", image_lookup=None)

    assert any(call["image"] == PIC[0] for call in images.calls)
    # The planner saw the caption, so a question with only "where is this?"
    # still searches for what the picture shows.
    assert "A bar chart" in chat.planner_client.prompts[0]
    # The user's own picture reaches the vision model, last.
    last = generator.calls[-1]["messages"][-1]
    assert last["images"][-1]["data"] == B64
    assert next(e for e in events if e["type"] == "meta")["citations"][0]["kind"] == "image"


async def test_an_image_search_failure_costs_the_pictures_not_the_answer():
    nlp = FakeNLP({"revenue chart": [hit("1")]})
    chat, _ = service(nlp, FakeImageIndex(fail=True))

    events = await collect(chat, question="revenue chart")

    assert events[-1]["type"] == "done"
    assert [c["chunk_order"] for c in next(e for e in events if e["type"] == "meta")["citations"]] == [1]


async def test_with_the_image_index_off_nothing_changes():
    nlp = FakeNLP({"revenue chart": [hit("1")]})
    chat, generator = service(nlp, None)

    await collect(chat, question="revenue chart")

    assert "images" not in generator.calls[-1]["messages"][-1]
