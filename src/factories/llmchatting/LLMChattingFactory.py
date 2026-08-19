from enums import LLMChattingProvider
from exceptions import UnsupportedProviderError
from utils import Settings, get_logger

from .AnthropicChatProvider import AnthropicChatProvider
from .CohereChatProvider import CohereChatProvider
from .GoogleChatProvider import GoogleChatProvider
from .LLMChattingInterface import LLMChattingInterface
from .OllamaChatProvider import OllamaChatProvider
from .OpenAIChatProvider import OpenAIChatProvider


def _thinking_flag(value: str) -> bool | str:
    """Turn the GENERATION_THINKING setting into what the SDK expects.

    "true"/"false" become booleans; "low"/"medium"/"high" pass through as the
    level string those models accept.
    """
    normalized = str(value).strip().lower()

    if normalized in ("true", "1", "yes", "on"):
        return True

    if normalized in ("false", "0", "no", "off", ""):
        return False

    return normalized


class LLMChattingFactory:
    """Builds the configured text-generation provider from ``Settings``.

    Holds the whole mapping from backend name to class and API key, so the rest
    of the application never names a vendor.
    """

    # provider -> the Settings attribute holding its key. Ollama is absent on
    # purpose: it runs locally and authenticates by host, not by key.
    _API_KEY_FIELDS = {
        LLMChattingProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
        LLMChattingProvider.OPENAI: "OPENAI_API_KEY",
        LLMChattingProvider.GOOGLE: "GOOGLE_API_KEY",
        LLMChattingProvider.COHERE: "COHERE_API_KEY",
    }

    _PROVIDERS = {
        LLMChattingProvider.ANTHROPIC: AnthropicChatProvider,
        LLMChattingProvider.OPENAI: OpenAIChatProvider,
        LLMChattingProvider.GOOGLE: GoogleChatProvider,
        LLMChattingProvider.COHERE: CohereChatProvider,
        LLMChattingProvider.OLLAMA: OllamaChatProvider,
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(type(self).__module__)

    def create(
        self, provider: LLMChattingProvider | str | None = None
    ) -> LLMChattingInterface:
        """Build the provider named by *provider*, or by GENERATION_BACKEND."""
        name = provider or self.settings.GENERATION_BACKEND

        try:
            # getattr(..., "value") first: str(SomeEnum.MEMBER) is
            # "SomeEnum.MEMBER", not "member", so a passed-in enum would
            # otherwise look like an unknown provider.
            chosen = LLMChattingProvider(str(getattr(name, "value", name)).strip().lower())
        except ValueError as exc:
            raise UnsupportedProviderError(
                f"Unknown chatting provider {name!r}. Supported: "
                f"{[p.value for p in LLMChattingProvider]}"
            ) from exc

        kwargs = {
            "model_id": self.settings.GENERATION_MODEL_ID,
            "default_max_tokens": self.settings.GENERATION_DEFAULT_MAX_TOKENS,
            "default_temperature": self.settings.GENERATION_DEFAULT_TEMPERATURE,
        }

        if chosen is LLMChattingProvider.OLLAMA:
            # Local: a host to reach, no key to check.
            kwargs["base_url"] = self.settings.OLLAMA_BASE_URL
            kwargs["thinking"] = _thinking_flag(self.settings.GENERATION_THINKING)
        else:
            api_key = getattr(self.settings, self._API_KEY_FIELDS[chosen])
            if not api_key:
                # Fail here, at startup, rather than on the first user request.
                raise UnsupportedProviderError(
                    f"{self._API_KEY_FIELDS[chosen]} is not set, so the "
                    f"{chosen.value!r} chatting provider cannot be built"
                )
            kwargs["api_key"] = api_key
            # Only OpenAI accepts a custom endpoint, and only when configured.
            if chosen is LLMChattingProvider.OPENAI and self.settings.OPENAI_API_BASE_URL:
                kwargs["base_url"] = self.settings.OPENAI_API_BASE_URL

        self.logger.info(
            "Building chatting provider %r (model=%r)",
            chosen.value,
            self.settings.GENERATION_MODEL_ID,
        )
        return self._PROVIDERS[chosen](**kwargs)
