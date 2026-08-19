from google import genai  # ty: ignore[unresolved-import]
from google.genai import types  # ty: ignore[unresolved-import]

from enums import ChatRole
from exceptions import LLMProviderError

from .LLMChattingInterface import LLMChattingInterface


class GoogleChatProvider(LLMChattingInterface):
    """Text generation via Gemini, through the unified ``google-genai`` SDK."""

    # Gemini calls the assistant "model"; the user role keeps its name.
    _ROLE_MAP = {
        ChatRole.USER.value: "user",
        ChatRole.ASSISTANT.value: "model",
    }

    def __init__(self, api_key: str, model_id: str, **kwargs) -> None:

        super().__init__(api_key=api_key, model_id=model_id, **kwargs)

        self.client = genai.Client(api_key=api_key)

    async def _generate_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:

        # Like Anthropic, Gemini takes the system prompt separately — here on
        # the config object rather than as a top-level kwarg.
        system, messages = self._split_system(messages)

        contents = [
            types.Content(
                role=self._ROLE_MAP[message["role"]],
                parts=[types.Part.from_text(text=message["content"])],
            )
            for message in messages
        ]

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_id, contents=contents, config=config
            )

        except Exception as exc:
            raise LLMProviderError(f"Google generation failed: {exc}") from exc

        usage = getattr(response, "usage_metadata", None)

        self._log_usage(
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "candidates_token_count", None),
        )

        # response.text is None when the answer was blocked by a safety filter
        # or truncated before the first token.
        text = response.text

        if not text:
            raise LLMProviderError(
                f"Google returned no text (model={self.model_id!r}, "
                f"prompt_feedback={getattr(response, 'prompt_feedback', None)!r})"
            )

        return text

    async def aclose(self) -> None:

        await self.client.aio.aclose()
