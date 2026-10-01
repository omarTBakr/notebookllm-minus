"""Fixed lookup tables translating this project's chat enums into vendor vocabularies.

Each provider speaks its own wire format for a concept this project already has
an enum for (ChatRole, LLMChattingProvider). These tables used to live as
private dicts on whichever class happened to consume them first; they live
beside the enums they key off instead.
"""

from .chatting import ChatRole, LLMChattingProvider

# Gemini calls the assistant "model"; the user role keeps its name.
CHAT_ROLE_TO_GOOGLE: dict[str, str] = {
    ChatRole.USER.value: "user",
    ChatRole.ASSISTANT.value: "model",
}

# provider -> the Settings attribute holding its key. Ollama is absent on
# purpose: it runs locally and authenticates by host, not by key.
CHAT_PROVIDER_API_KEY_FIELDS: dict[LLMChattingProvider, str] = {
    LLMChattingProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    LLMChattingProvider.OPENAI: "OPENAI_API_KEY",
    LLMChattingProvider.GOOGLE: "GOOGLE_API_KEY",
    LLMChattingProvider.COHERE: "COHERE_API_KEY",
    LLMChattingProvider.NVIDIA: "NVIDIA_API_KEY",
    LLMChattingProvider.OPENROUTER: "OPENROUTER_API_KEY",
}

# provider -> {constructor keyword: the Settings field that fills it}.
#
# Everything a provider needs beyond the three knobs every provider takes
# (model, max tokens, temperature) and its API key. The factory walks this
# table instead of growing an `if chosen is ...` per vendor, so giving a
# provider a new knob is a line here plus a field on Settings — no factory
# edit, and nothing vendor-specific baked into a provider class.
#
# A field that is unset or blank is simply not passed, leaving the provider's
# own signature default in charge. Ollama is absent: its base URL is a derived
# property (ollama_base_url), not a plain field, so the factory builds it.
CHAT_PROVIDER_SETTING_KWARGS: dict[LLMChattingProvider, dict[str, str]] = {
    LLMChattingProvider.ANTHROPIC: {"workspace_id": "ANTHROPIC_WORKSPACE_ID"},
    LLMChattingProvider.OPENAI: {"base_url": "OPENAI_API_BASE_URL"},
    LLMChattingProvider.NVIDIA: {"base_url": "NVIDIA_API_BASE_URL"},
    LLMChattingProvider.OPENROUTER: {"base_url": "OPENROUTER_API_BASE_URL"},
}
