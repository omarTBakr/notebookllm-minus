"""Model-based guardrails, on LangChain's AgentMiddleware contract.

``InputGuardrail`` implements ``abefore_model`` (runs before the answer model
sees the question) and ``OutputGuardrail`` implements ``aafter_model`` (runs on
what the model wrote). On a flagged verdict each returns LangChain's standard
early-exit update:

    {"jump_to": "end", "messages": [AIMessage(refusal)], "guardrail_verdict": {...}}

This app does not run a LangChain agent: ChatService calls the hooks directly
with a minimal ``{"messages": [...]}`` state. Keeping the contract means the
same classes drop into ``create_agent(middleware=[...])`` unchanged when the
answer path becomes a full agent.

The verdict itself comes from a separate guard model, through a dispatcher.
In production that is a Celery queue per stage with micro-batching (see
``application/tasks/jobs/guardrails.py``); tests pass an in-process one.
"""

import asyncio
from abc import ABC
from time import perf_counter
from typing import Any, ClassVar, NotRequired, Protocol

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from application.prompts import TemplateParser
from shared.utils import get_logger, get_settings, tracing
from shared.utils.metrics import GUARDRAIL_SECONDS, GUARDRAIL_VERDICTS

from .verdict import Verdict

logger = get_logger(__name__)


class GuardrailState(AgentState):
    """AgentState plus what the guardrails read and write."""

    #: The verdict that stopped the run, for the caller to report.
    guardrail_verdict: NotRequired[dict]
    #: Output stage: the answer text already shown, so a passage is judged in
    #: context rather than as a fragment.
    guardrail_context: NotRequired[str]


class GuardrailDispatcher(Protocol):
    """Gets a verdict for one item from wherever the guard model runs."""

    async def submit(self, stage: str, item: dict) -> dict: ...


class ModelGuardrail(AgentMiddleware, ABC):
    """A guardrail whose decision comes from a guard model.

    One instance per request: ``lang`` picks the refusal text, and nothing
    else is held between calls.
    """

    state_schema = GuardrailState

    #: "input" or "output". Names the queue, the prompt and the metric label.
    stage: ClassVar[str]
    refusal_key: ClassVar[str]

    def __init__(
        self,
        dispatcher: GuardrailDispatcher,
        *,
        lang: str | None = None,
        timeout: float | None = None,
        fail_mode: str | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.dispatcher = dispatcher
        self.lang = lang or settings.DEFAULT_LANG
        self.timeout = timeout if timeout is not None else settings.GUARDRAIL_TIMEOUT_S
        self.fail_mode = fail_mode or settings.GUARDRAIL_FAIL_MODE

    @property
    def name(self) -> str:
        return f"{self.stage}_guardrail"

    # --- the decision ---------------------------------------------------------

    async def judge(self, item: dict) -> Verdict:
        """Ask the guard model about *item*, resolving "undecided" by fail mode."""
        started = perf_counter()

        with tracing.observation(
            f"guardrail.{self.stage}",
            as_type="guardrail",
            input=item,
            metadata={"fail_mode": self.fail_mode},
        ) as span:
            try:
                raw = await asyncio.wait_for(
                    self.dispatcher.submit(self.stage, {**item, "lang": self.lang}),
                    timeout=self.timeout,
                )
                verdict = Verdict.from_dict(raw)
            except Exception as exc:
                verdict = Verdict.undecided(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)

            verdict = self.resolve(verdict)

            GUARDRAIL_SECONDS.labels(self.stage).observe(perf_counter() - started)
            GUARDRAIL_VERDICTS.labels(
                self.stage, verdict.category, "fail_mode" if verdict.by_fail_mode else "model"
            ).inc()
            span.update(
                output=verdict.to_dict(),
                level="DEFAULT" if verdict.safe and not verdict.by_fail_mode else "WARNING",
            )

        if verdict.by_fail_mode:
            logger.warning(
                "%s guardrail could not decide (%s); fail mode %s %s it",
                self.stage,
                verdict.reason,
                self.fail_mode,
                "passed" if verdict.safe else "refused",
            )
        elif not verdict.safe:
            logger.info("%s guardrail flagged a message (category=%s)", self.stage, verdict.category)

        return verdict

    def resolve(self, verdict: Verdict) -> Verdict:
        if verdict.safe is not None:
            return verdict
        return Verdict(
            safe=self.fail_mode == "open",
            category="undecided",
            reason=verdict.reason,
            by_fail_mode=True,
        )

    def refusal_text(self) -> str:
        return TemplateParser(lang=self.lang, default_lang=get_settings().DEFAULT_LANG).get(
            "guardrails", self.refusal_key
        )

    def _stop(self, verdict: Verdict, replaces: Any = None) -> dict:
        """LangChain's early exit, with the refusal as the final message.

        Given the flagged message, the refusal takes its id, so the add_messages
        reducer *replaces* it instead of appending after it: the unsafe text
        does not stay in the agent's state.
        """
        message_id = getattr(replaces, "id", None)
        refusal = AIMessage(content=self.refusal_text(), **({"id": message_id} if message_id else {}))
        return {"jump_to": "end", "messages": [refusal], "guardrail_verdict": verdict.to_dict()}

    @staticmethod
    def last(messages: list, kind: type) -> Any:
        return next((m for m in reversed(messages or []) if isinstance(m, kind)), None)


class InputGuardrail(ModelGuardrail):
    """Before the model: refuse a harmful, injected or jailbreak question."""

    stage = "input"
    refusal_key = "refusal_input"

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: GuardrailState, runtime: Any = None) -> dict | None:
        message = self.last(state.get("messages"), HumanMessage)
        if message is None or not str(message.content).strip():
            return None

        verdict = await self.judge({"text": str(message.content)})
        return None if verdict.safe else self._stop(verdict)


class OutputGuardrail(ModelGuardrail):
    """After the model: withhold harmful text, leaked personal data or a leaked
    system prompt. On the streamed path it judges one passage at a time; see
    application/guardrails/stream.py."""

    stage = "output"
    refusal_key = "refusal_output"

    @hook_config(can_jump_to=["end"])
    async def aafter_model(self, state: GuardrailState, runtime: Any = None) -> dict | None:
        answer = self.last(state.get("messages"), AIMessage)
        if answer is None or not str(answer.content).strip():
            return None

        question = self.last(state.get("messages"), HumanMessage)
        verdict = await self.judge(
            {
                "text": str(answer.content),
                "question": str(question.content) if question is not None else "",
                "context": state.get("guardrail_context", ""),
            }
        )
        return None if verdict.safe else self._stop(verdict, replaces=answer)

    async def check_passage(self, question: str, context: str, passage: str) -> Verdict:
        """The streamed path's call: run the after_model hook on one passage."""
        update = await self.aafter_model(
            {
                "messages": [HumanMessage(content=question), AIMessage(content=passage)],
                "guardrail_context": context,
            }
        )
        if update is None:
            return Verdict(safe=True)
        return Verdict.from_dict(update.get("guardrail_verdict"))
