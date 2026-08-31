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
