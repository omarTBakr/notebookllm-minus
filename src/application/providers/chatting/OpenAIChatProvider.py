from collections.abc import AsyncIterator

from openai import AsyncOpenAI  # ty: ignore[unresolved-import]

from shared.exceptions import LLMProviderError

from .LLMChattingInterface import LLMChattingInterface


class OpenAIChatProvider(LLMChattingInterface):
    """Text generation via OpenAI's Chat Completions API.

    ``base_url`` is exposed so any OpenAI-compatible endpoint (a local server,
    a gateway, another vendor's compatibility layer) works through this same
    class with no code change — see :class:`NvidiaChatProvider`, which is
    this class plus a name.
    """

    # Whose endpoint this is, for the error messages only. A subclass pointed
    # at another vendor's compatibility layer says that vendor's name instead,
    # so "OpenAI generation failed" never turns up for a call that never went
    # anywhere near OpenAI.
    _VENDOR = "OpenAI"

    # What this endpoint calls the output cap. OpenAI deprecated max_tokens and
    # *requires* max_completion_tokens on its reasoning models, so that is the
    # default — but it is not universal across OpenAI-compatible servers, and a
    # server whose schema forbids unknown fields answers 400 extra_forbidden
    # rather than ignoring it. Hence a name a subclass can change.
    _MAX_TOKENS_FIELD = "max_completion_tokens"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
        thinking: bool | None = None,
        **kwargs,
    ) -> None:

        super().__init__(api_key=api_key, model_id=model_id, **kwargs)

        # None means "say nothing about it" and is the default: the vendor's
        # own behaviour for the model, which is what ordinary chat wants.
        # False turns a reasoning model's scratchpad off.
        #
        # It is not a preference, it is a correctness fix for structured
        # output. Asked to repair a page of Arabic and return JSON,
        # nemotron-3-super spent its entire token budget reasoning in prose and
        # emitted no JSON at all -- 15-18k characters of thinking, three failed
        # parses, 429s, and the page rejected. The same call with thinking off
        # returns valid JSON in 12s. See TextCorrectionService.
        self.thinking = thinking

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _extra_body(self) -> dict:
        """Vendor-specific request fields the OpenAI schema has no place for.

        `chat_template_kwargs` is how NVIDIA NIM (and vLLM behind it) exposes a
        reasoning model's think toggle. Sent only when asked for, so a model
        that has never heard of it is not handed an unknown field.
        """
        if self.thinking is None:
            return {}

        return {"extra_body": {"chat_template_kwargs": {"thinking": self.thinking}}}

    async def _generate_text(self, messages: list[dict], max_tokens: int, temperature: float) -> str:

        # OpenAI takes the system turn inline, so the neutral format arrives
        # ready to send.
        try:
            response = await self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                temperature=temperature,
                **{self._MAX_TOKENS_FIELD: max_tokens},
                **self._extra_body(),
            )

        except Exception as exc:
            raise LLMProviderError(f"{self._VENDOR} generation failed: {exc}") from exc

        usage = getattr(response, "usage", None)

        self._log_usage(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))

        if not response.choices:
            raise LLMProviderError(f"{self._VENDOR} returned no choices (model={self.model_id!r})")

        choice = response.choices[0]

        text = choice.message.content

        if not text:
            # Empty content with finish_reason="length" means the answer was
            # cut off before any token landed — worth naming in the message.
            raise LLMProviderError(
                f"{self._VENDOR} returned no text (model={self.model_id!r}, " f"finish_reason={choice.finish_reason!r})"
            )

        return text

    async def _stream_text(self, messages: list[dict], max_tokens: int, temperature: float) -> AsyncIterator[dict]:
        """The same call with ``stream=True``, in pieces as they arrive.

        Without this the interface's fallback applies: generate the whole
        answer, yield it once. Correct, but the UI then sits empty for the
        length of the reply and a reasoning model's scratchpad is never shown
        at all, because a finished answer no longer has one.

        Reasoning arrives on its own field. OpenAI-compatible servers put it
        in ``reasoning_content`` beside ``content`` — NVIDIA's reasoning NIMs
        fill both in the same stream — so the two map straight onto the
        thinking/content split the interface already defines, and nothing
        needs to parse tags out of the answer text.
        """
        try:
            stream = await self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                temperature=temperature,
                stream=True,
                **{self._MAX_TOKENS_FIELD: max_tokens},
                # Usage is omitted from a streamed response unless asked for,
                # and _log_usage is the only reason this provider looks at it.
                stream_options={"include_usage": True},
            )

            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage:
                    self._log_usage(
                        getattr(usage, "prompt_tokens", None),
                        getattr(usage, "completion_tokens", None),
                    )

                if not chunk.choices:
                    # The usage-only frame arrives after the last choice.
                    continue

                delta = chunk.choices[0].delta

                # NIM and vLLM name the field reasoning_content; OpenRouter
                # calls it reasoning. Neither is in the OpenAI schema, so both
                # arrive as extras on the delta.
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning:
                    yield {"kind": "thinking", "text": reasoning}

                # Empty deltas are ordinary — the first frame carries only the
                # role, the last only a finish reason.
                if delta.content:
                    yield {"kind": "content", "text": delta.content}

        except Exception as exc:
            # Raised mid-iteration, once the caller is already consuming, so
            # it still has to be the same error type a non-streamed failure
            # produces or the route's error frame would differ by transport.
            raise LLMProviderError(f"{self._VENDOR} streaming failed: {exc}") from exc
