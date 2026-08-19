import cohere  # ty: ignore[unresolved-import]

from exceptions import LLMProviderError

from ..cohere_support import aclose_cohere_client
from .LLMChattingInterface import LLMChattingInterface


class CohereChatProvider(LLMChattingInterface):
    """Text generation via Cohere's v2 Chat API."""

    def __init__(self, api_key: str, model_id: str, **kwargs) -> None:

        super().__init__(api_key=api_key, model_id=model_id, **kwargs)

        self.client = cohere.AsyncClientV2(api_key=api_key)

    async def _generate_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:

        # v2 took the OpenAI message shape, so the neutral format arrives ready
        # to send — system turns included.
        try:
            response = await self.client.chat(
                model=self.model_id,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        except Exception as exc:
            raise LLMProviderError(f"Cohere generation failed: {exc}") from exc

        tokens = getattr(getattr(response, "usage", None), "tokens", None)

        self._log_usage(
            getattr(tokens, "input_tokens", None), getattr(tokens, "output_tokens", None)
        )

        # message.content is a list of blocks; join the text ones and skip
        # anything else (tool plans, citations) that carries no answer.
        blocks = response.message.content or []

        text = "".join(block.text for block in blocks if getattr(block, "type", None) == "text")

        if not text:
            raise LLMProviderError(
                f"Cohere returned no text (model={self.model_id!r}, "
                f"finish_reason={response.finish_reason!r})"
            )

        return text

    async def aclose(self) -> None:

        await aclose_cohere_client(self.client)
