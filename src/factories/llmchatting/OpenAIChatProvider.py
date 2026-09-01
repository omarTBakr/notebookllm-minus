from collections.abc import AsyncIterator

from openai import AsyncOpenAI  # ty: ignore[unresolved-import]

from exceptions import LLMProviderError

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

    def __init__(
        self, api_key: str, model_id: str, base_url: str | None = None, **kwargs
    ) -> None:

        super().__init__(api_key=api_key, model_id=model_id, **kwargs)

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def _generate_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:

        # OpenAI takes the system turn inline, so the neutral format arrives
        # ready to send.
        try:
            response = await self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                # max_completion_tokens, not the deprecated max_tokens.
                max_completion_tokens=max_tokens,
                temperature=temperature,
            )

        except Exception as exc:
            raise LLMProviderError(
                f"{self._VENDOR} generation failed: {exc}"
            ) from exc

        usage = getattr(response, "usage", None)

        self._log_usage(
            getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
        )

        if not response.choices:
            raise LLMProviderError(
                f"{self._VENDOR} returned no choices (model={self.model_id!r})"
            )

        choice = response.choices[0]

        text = choice.message.content

        if not text:
            # Empty content with finish_reason="length" means the answer was
            # cut off before any token landed — worth naming in the message.
            raise LLMProviderError(
                f"{self._VENDOR} returned no text (model={self.model_id!r}, "
                f"finish_reason={choice.finish_reason!r})"
            )

        return text

    async def _stream_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> AsyncIterator[dict]:
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
                max_completion_tokens=max_tokens,
                temperature=temperature,
                stream=True,
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

                reasoning = getattr(delta, "reasoning_content", None)
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
