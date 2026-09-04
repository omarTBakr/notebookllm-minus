from anthropic import AsyncAnthropic  # ty: ignore[unresolved-import]

from exceptions import LLMProviderError

from .LLMChattingInterface import LLMChattingInterface


class AnthropicChatProvider(LLMChattingInterface):
    """Text generation via Anthropic's Messages API."""

    def __init__(self, api_key: str, model_id: str, workspace_id: str | None = None, **kwargs) -> None:

        super().__init__(api_key=api_key, model_id=model_id, **kwargs)

        # An identity-linked key is scoped to a workspace and Anthropic refuses
        # the request without naming one — a 400 before any generation happens.
        # An ordinary key has no workspace, and sending the header empty is
        # itself an error, so it is added only when configured.
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None

        self.client = AsyncAnthropic(api_key=api_key, default_headers=headers)

    async def _generate_text(self, messages: list[dict], max_tokens: int, temperature: float) -> str:

        # Anthropic takes the system prompt as its own argument and rejects a
        # "system" role inside messages.
        system, messages = self._split_system(messages)

        # Unlike the other three, max_tokens is required here, not optional.
        request = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system:
            request["system"] = system

        try:
            response = await self.client.messages.create(**request)

        except Exception as exc:
            raise LLMProviderError(f"Anthropic generation failed: {exc}") from exc

        usage = getattr(response, "usage", None)

        self._log_usage(getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))

        # content is a list of typed blocks; only the text ones carry an answer,
        # and a tool-use or thinking block would have no .text at all.
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")

        if not text:
            raise LLMProviderError(
                f"Anthropic returned no text (model={self.model_id!r}, " f"stop_reason={response.stop_reason!r})"
            )

        return text
