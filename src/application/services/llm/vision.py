"""Whether a chat model can look at pictures.

Asked once per answer that has pictures to show, so it is cached: a model's
capabilities do not change while the process runs, and the listing calls
behind the answer (Ollama's /api/show, a vendor's model list) take tens to
hundreds of milliseconds.

The answer comes from the same place the model picker's "image" tag does:
Ollama reports "vision" in a model's capabilities, and OpenRouter's listing
does once ``input_modalities`` contains "image" (see OpenRouterModelService).
NVIDIA publishes neither, so its vision models are recognised by name, the
way NVIDIA names them. Anthropic and Google models of the generations this
app supports all take images.
"""

from __future__ import annotations

import time

import httpx

from shared.enums import ModelCapability
from shared.utils import get_logger, get_settings
from shared.utils.model_ids import (
    ANTHROPIC,
    GOOGLE,
    NVIDIA,
    OLLAMA_SOURCES,
    default_chat_model,
    host_for,
    split_source,
)

logger = get_logger(__name__)

_TTL_S = 3600.0
_cache: dict[str, tuple[float, bool]] = {}

_NVIDIA_VISION_HINTS = ("vision", "-vl", "vlm", "multimodal", "neva", "vila")


async def _ollama_has_vision(settings, source: str, tag: str) -> bool:
    url = host_for(settings, source).rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"{url}/api/show", json={"model": tag})
        response.raise_for_status()
        return ModelCapability.VISION.value in (response.json().get("capabilities") or [])


async def _listed_has_vision(source: str, model_id: str) -> bool:
    from . import for_source

    for model in await for_source(source).list_models():
        if model.get("id") == model_id:
            return ModelCapability.VISION.value in (model.get("capabilities") or [])
    return False


async def has_vision(model_id: str | None) -> bool:
    """Whether *model_id* (or the default chat model) accepts images. Never raises."""
    settings = get_settings()
    resolved = model_id or default_chat_model(settings)

    cached = _cache.get(resolved)
    if cached is not None and time.monotonic() - cached[0] < _TTL_S:
        return cached[1]

    source, tag = split_source(resolved)
    try:
        if source in OLLAMA_SOURCES:
            answer = await _ollama_has_vision(settings, source, tag)
        elif source == NVIDIA:
            answer = any(hint in tag.lower() for hint in _NVIDIA_VISION_HINTS)
        elif source in (ANTHROPIC, GOOGLE):
            answer = True
        else:
            answer = await _listed_has_vision(source, resolved)
    except Exception as exc:  # noqa: BLE001 - unknown means "send the caption", which always works
        logger.info("Could not tell whether %r has vision (%s); assuming not", resolved, exc)
        return False

    _cache[resolved] = (time.monotonic(), answer)
    return answer
