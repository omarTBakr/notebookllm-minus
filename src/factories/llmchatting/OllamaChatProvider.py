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

    def __init__(self, model_id: str, base_url: str, **kwargs) -> None:

        super().__init__(model_id=model_id, **kwargs)

        self.base_url = base_url

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

    async def aclose(self) -> None:

        await self.client.close()
