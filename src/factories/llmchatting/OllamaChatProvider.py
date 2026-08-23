from collections.abc import AsyncIterator

from ollama import AsyncClient  # ty: ignore[unresolved-import]

from exceptions import LLMProviderError

from .LLMChattingInterface import LLMChattingInterface


class OllamaChatProvider(LLMChattingInterface):
    """Text generation against a local Ollama server.

    The odd one out in two ways: there is no API key — reaching the host is
    the only authentication — and the whole thing runs on the user's own
    machine, so a connection error usually means "ollama serve is not running"
    rather than an outage.
    """

    def __init__(
        self, model_id: str, base_url: str, thinking: bool | str = False, **kwargs
    ) -> None:

        super().__init__(model_id=model_id, **kwargs)

        self.base_url = base_url

        # True, or one of "low"/"medium"/"high" for models that take a level.
        # Only a request: _open_stream falls back when the model refuses.
        self.thinking = thinking

        self.client = AsyncClient(host=base_url)

    async def _generate_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:

        # Ollama takes the system turn inline, so the neutral format arrives
        # ready to send.
        try:
            response = await self.client.chat(
                model=self.model_id,
                messages=messages,
                # Generation knobs live under `options`, and the token cap is
                # called num_predict rather than max_tokens.
                options={"num_predict": max_tokens, "temperature": temperature},
            )

        except Exception as exc:
            raise LLMProviderError(
                f"Ollama generation failed at {self.base_url}: {exc} "
                "(is `ollama serve` running, and has the model been pulled?)"
            ) from exc

        self._log_usage(
            getattr(response, "prompt_eval_count", None), getattr(response, "eval_count", None)
        )

        text = response.message.content

        if not text:
            raise LLMProviderError(
                f"Ollama returned no text (model={self.model_id!r}, "
                f"done_reason={response.done_reason!r})"
            )

        return text

    async def _open_stream(self, messages: list[dict], options: dict, think):
        """Start the stream, dropping `think` if the model rejects it.

        Whether a model reasons is a property of the model, not of the config,
        and Ollama errors rather than ignoring the flag. Retrying once without
        it means switching to a non-reasoning model needs no .env change.
        """
        request = {
            "model": self.model_id,
            "messages": messages,
            "stream": True,
            "options": options,
        }

        if not think:
            return await self.client.chat(**request)

        try:
            return await self.client.chat(think=think, **request)

        except Exception as exc:
            self.logger.info(
                "Model %r rejected think=%r (%s); streaming without it",
                self.model_id,
                think,
                str(exc)[:120],
            )
            self._thinking_supported = False
            return await self.client.chat(**request)

    async def _stream_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> AsyncIterator[dict]:

        options = {"num_predict": max_tokens, "temperature": temperature}

        think = self.thinking if getattr(self, "_thinking_supported", True) else False

        try:
            stream = await self._open_stream(messages, options, think)

            async for part in stream:
                # Reasoning models fill `thinking` before they fill `content`.
                # Both are forwarded so the UI can show the scratchpad while
                # the answer is still being worked out.
                reasoning = getattr(part.message, "thinking", None)
                if reasoning:
                    yield {"kind": "thinking", "text": reasoning}

                # The final part carries the timing totals and an empty
                # content; skipping empties keeps them out of the answer.
                if part.message.content:
                    yield {"kind": "content", "text": part.message.content}

        except Exception as exc:
            # Raised mid-iteration once the caller has already begun consuming.
            # Still LLMProviderError, so the route's error frame reports the
            # same type it would for a non-streaming failure.
            raise LLMProviderError(
                f"Ollama streaming failed at {self.base_url}: {exc} "
                "(is `ollama serve` running, and has the model been pulled?)"
            ) from exc
