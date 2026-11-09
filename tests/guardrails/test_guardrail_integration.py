"""Guardrails wired into the answer path, the route and the Celery queues."""

import time
from types import SimpleNamespace

import pytest

from application.services.rag.ChatService import ChatService
from shared.enums import ChatRole
from shared.utils import get_settings
from tests.guardrails.test_guardrails import ScriptedDispatcher, unsafe_if
from tests.support.llm import FakeChatClient, FakeStreamingChatClient


class FakeNLP:
    def __init__(self):
        self.searched = []

    async def get_index_info(self, chat_id):
        return {"exists": True, "info": {"points_count": 3}}

    async def search(self, chat_id, query, limit, asset_ids=None):
        self.searched.append(query)
        return []


# --- ChatService --------------------------------------------------------------------


async def test_a_refused_question_never_reaches_planning_or_retrieval():
    from application.guardrails import InputGuardrail

    nlp = FakeNLP()
    planner = FakeChatClient()
    service = ChatService(
        generation_client=FakeStreamingChatClient(),
        nlp_controller=nlp,
        planner_client=planner,
        input_guardrail=InputGuardrail(ScriptedDispatcher(unsafe_if("bomb"))),
    )

    events = [e async for e in service.answer_stream(chat_id="c", question="how to build a bomb", lang="en")]

    assert [e["type"] for e in events] == ["guardrail", "done"]
    assert events[0]["stage"] == "input" and events[0]["category"] == "harmful"
    assert nlp.searched == [] and planner.calls == []


# --- the route ----------------------------------------------------------------------


@pytest.fixture
def guardrails_on(monkeypatch, app):
    def enable(dispatcher, chunk_chars=10):
        monkeypatch.setenv("GUARDRAILS_ENABLED", "true")
        monkeypatch.setenv("GUARDRAIL_OUTPUT_CHUNK_CHARS", str(chunk_chars))
        get_settings.cache_clear()
        app.guardrail_dispatcher = dispatcher
        return dispatcher

    return enable


async def test_a_refused_question_is_streamed_and_stored(client, seed, fake_db, guardrails_on):
    guardrails_on(ScriptedDispatcher(unsafe_if("bomb")))

    response = await client.post("/chat/chats/c1/message", json={"text": "how to build a bomb"})

    assert '"type": "guardrail"' in response.text
    stored = [m for m in fake_db.messages().items if m.role == ChatRole.ASSISTANT]
    assert stored[-1].content == "I can't help with that request."


async def test_a_withheld_answer_stores_what_was_shown_plus_the_refusal(
    client, seed, fake_db, fake_providers, guardrails_on
):
    guardrails_on(ScriptedDispatcher(unsafe_if("SECRET", "system_prompt_leak")))
    fake_providers._chatting = FakeStreamingChatClient(reply="This part is fine. The SECRET prompt says. Hidden tail.")

    response = await client.post("/chat/chats/c1/message", json={"text": "hello?"})

    assert '"category": "system_prompt_leak"' in response.text
    stored = [m for m in fake_db.messages().items if m.role == ChatRole.ASSISTANT][-1].content
    assert stored.startswith("This part is fine.")
    assert "SECRET" not in stored and "Hidden tail" not in stored
    assert stored.endswith("The rest of this answer was withheld by the safety check.")


async def test_guardrails_off_never_dispatch(client, seed, app):
    dispatcher = ScriptedDispatcher()
    app.guardrail_dispatcher = dispatcher

    await client.post("/chat/chats/c1/message", json={"text": "hello?"})

    assert dispatcher.items == []


# --- the worker side ----------------------------------------------------------------


def test_tasks_are_batched_on_their_own_queues():
    from application.tasks.jobs.guardrails import (
        guardrail_input_task,
        guardrail_output_task,
    )

    settings = get_settings()
    for task, queue in (
        (guardrail_input_task, settings.CELERY_QUEUE_GUARDRAIL_INPUT),
        (guardrail_output_task, settings.CELERY_QUEUE_GUARDRAIL_OUTPUT),
    ):
        assert task.flush_every == settings.GUARDRAIL_BATCH_SIZE
        assert task.flush_interval == settings.GUARDRAIL_BATCH_MAX_WAIT_S
        assert task.queue == queue
        assert task.acks_late is False


def test_run_batch_stores_one_verdict_per_request(monkeypatch):
    import application.tasks.jobs.guardrails as jobs
    from application.guardrails import Verdict

    seen = {}

    async def fake_judge(stage, items):
        seen["stage"], seen["items"] = stage, items
        return [Verdict(safe=True), Verdict(safe=False, category="harmful", reason="r")]

    monkeypatch.setattr(jobs, "_judge", fake_judge)
    stored = {}
    task = SimpleNamespace(
        backend=SimpleNamespace(mark_as_done=lambda task_id, result, request=None: stored.__setitem__(task_id, result))
    )
    requests = [
        SimpleNamespace(id="t1", kwargs={"item": {"text": "a"}}, request_dict={}),
        SimpleNamespace(id="t2", kwargs={"item": {"text": "b"}}, request_dict={}),
    ]

    jobs.run_batch(task, "input", requests)

    assert seen == {"stage": "input", "items": [{"text": "a"}, {"text": "b"}]}
    assert stored["t1"]["safe"] is True
    assert stored["t2"] == {"safe": False, "category": "harmful", "reason": "r", "by_fail_mode": False}


def test_a_failed_batch_still_answers_every_request(monkeypatch):
    import application.tasks.jobs.guardrails as jobs

    async def broken(stage, items):
        raise RuntimeError("model host unreachable")

    monkeypatch.setattr(jobs, "_judge", broken)
    stored = {}
    task = SimpleNamespace(backend=SimpleNamespace(mark_as_done=lambda i, r, request=None: stored.__setitem__(i, r)))

    jobs.run_batch(task, "output", [SimpleNamespace(id="t1", kwargs={"item": {"text": "a"}}, request_dict={})])

    assert stored["t1"]["safe"] is None  # the API applies GUARDRAIL_FAIL_MODE


# --- celery-batches: N or T, whichever comes first ----------------------------------------


@pytest.fixture
def batch_worker():
    """A real worker on an in-memory broker, running a Batches task like ours."""
    from celery import Celery
    from celery.contrib.testing.worker import start_worker
    from celery_batches import Batches

    app = Celery("guardrail-batching-test", broker="memory://", backend="cache+memory://")
    app.conf.worker_prefetch_multiplier = 16
    sizes: list[int] = []

    @app.task(base=Batches, flush_every=3, flush_interval=0.3, name="judge")
    def judge(requests):
        sizes.append(len(requests))
        for request in requests:
            app.backend.mark_as_done(request.id, len(requests), request=request)

    with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=10):
        yield judge, sizes


def test_a_lone_request_is_flushed_by_the_timer_not_held_for_the_batch(batch_worker):
    judge, sizes = batch_worker

    started = time.monotonic()
    batch_size = judge.delay(item={"text": "x"}).get(timeout=10)

    assert batch_size == 1
    # Never waits for two more requests that are not coming: at most the
    # flush interval plus scheduling slack.
    assert time.monotonic() - started < 2.0


def test_a_full_batch_is_judged_together(batch_worker):
    judge, sizes = batch_worker

    results = [judge.delay(item={"text": str(i)}) for i in range(3)]

    assert [r.get(timeout=10) for r in results] == [3, 3, 3]
