from enum import Enum


class LLMChattingProvider(str, Enum):
    """Text-generation backends this application knows how to build."""

    ANTHROPIC = "anthropic"
    OPENAI    = "openai"
    GOOGLE    = "google"
    COHERE    = "cohere"
    OLLAMA    = "ollama"  # local models; no API key, needs OLLAMA_HOST/PORT


class ThinkingLevel(str, Enum):
    """How much reasoning to ask a model to expose before it answers.

    Ollama-only: every other provider ignores it, and it is dropped
    automatically for models that do not support it. TRUE/FALSE become
    booleans on the way into the SDK; the three levels pass through as-is.
    """

    TRUE   = "true"
    FALSE  = "false"
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"

    # .env files spell yes/no every which way, and none of those spellings is
    # a member of this enum on its own — so Settings resolves them here before
    # pydantic ever checks the value against TRUE/FALSE/LOW/MEDIUM/HIGH.
    # Mirrors AssetType.from_content_type / AssetType._mime_map_: the mapping
    # from an arbitrary raw value to a member belongs on the enum, not
    # floating in the settings module that happens to be the first caller.
    #
    # An annotation only, not an assignment — Enum treats any *assigned* name
    # here as a candidate member, aliases dict included, which would silently
    # add a third, bogus entry to `list(ThinkingLevel)`. Populated below,
    # once the class (and its real members) already exist.
    _ALIASES: dict

    @classmethod
    def from_alias(cls, value: object) -> object:
        """*value* translated through the alias table, or returned as-is.

        Deliberately permissive: an already-valid member, an unrecognised
        string, or a non-string all pass straight through unchanged, and it
        is pydantic's own enum check — not this method — that rejects
        whatever is left over.
        """
        return cls._ALIASES.get(value, value) if isinstance(value, str) else value


ThinkingLevel._ALIASES = {
    "1": ThinkingLevel.TRUE, "yes": ThinkingLevel.TRUE, "on": ThinkingLevel.TRUE,
    "0": ThinkingLevel.FALSE, "no": ThinkingLevel.FALSE, "off": ThinkingLevel.FALSE,
    "": ThinkingLevel.FALSE,
}


class ChatRole(str, Enum):
    """Provider-neutral message roles.

    Callers speak only these three; each provider translates on the way out —
    Google renames ASSISTANT to "model", and Anthropic and Google both lift
    SYSTEM out of the message list entirely.
    """

    SYSTEM    = "system"
    USER      = "user"
    ASSISTANT = "assistant"
