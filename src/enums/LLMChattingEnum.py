from enum import Enum


class LLMChattingProvider(str, Enum):
    """Text-generation backends this application knows how to build."""

    ANTHROPIC = "anthropic"
    OPENAI    = "openai"
    GOOGLE    = "google"
    COHERE    = "cohere"
    OLLAMA    = "ollama"  # local models; no API key, needs OLLAMA_BASE_URL


class ChatRole(str, Enum):
    """Provider-neutral message roles.

    Callers speak only these three; each provider translates on the way out —
    Google renames ASSISTANT to "model", and Anthropic and Google both lift
    SYSTEM out of the message list entirely.
    """

    SYSTEM    = "system"
    USER      = "user"
    ASSISTANT = "assistant"
