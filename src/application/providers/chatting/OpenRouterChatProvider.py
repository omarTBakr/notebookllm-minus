from .OpenAIChatProvider import OpenAIChatProvider


class OpenRouterChatProvider(OpenAIChatProvider):
    """Text generation via OpenRouter.

    OpenRouter serves the OpenAI Chat Completions API in front of models from
    many publishers, so like :class:`NvidiaChatProvider` this is the vendor's
    *name* plus the two places its dialect differs:

    * the output cap is ``max_tokens``. OpenRouter normalises it for each
      underlying model, where ``max_completion_tokens`` is an OpenAI-only name
      that not every route it forwards to understands;
    * the think toggle is ``reasoning: {"enabled": ...}``, not NIM's
      ``chat_template_kwargs``. OpenRouter maps it onto whatever the chosen
      model calls reasoning, and ignores it for a model that has none.

    A model id is ``<publisher>/<model>`` ("anthropic/claude-sonnet-4"), and the
    application's own prefix goes in front of it: ``openrouter/anthropic/...``.
    """

    _VENDOR = "OpenRouter"

    _MAX_TOKENS_FIELD = "max_tokens"

    def _extra_body(self) -> dict:
        if self.thinking is None:
            return {}

        return {"extra_body": {"reasoning": {"enabled": bool(self.thinking)}}}
