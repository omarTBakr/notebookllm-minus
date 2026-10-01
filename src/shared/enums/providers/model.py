"""Model capability and catalogue filtering constants."""

from enum import StrEnum


class ModelCapability(StrEnum):
    """Capabilities reported by model providers."""

    COMPLETION = "completion"
    EMBEDDING = "embedding"
    VISION = "vision"
    TOOLS = "tools"
    THINKING = "thinking"


class NvidiaSafetyModelMarker(StrEnum):
    """Name fragments identifying NVIDIA safety-only models."""

    GUARD = "guard"
    SAFETY = "safety"
    MODERATION = "moderation"
    CONTENT_SAFETY = "content-safety"
    CONTENT_SAFETY_UNDERSCORE = "content_safety"
    LLAMA_31_NEMOGUARD = "llama-3.1-nemoguard"
    LLAMA_32_NEMOGUARD = "llama-3.2-nemoguard"
    SHIELD = "shield"
