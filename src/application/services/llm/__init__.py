"""Talking to a model, as opposed to doing anything with the answer.

`ModelService` discovers what a provider can actually be asked for — which
matters more here than it sounds, because a catalogue listing a model is not
the same as an account being allowed to call it. `structured_generation` asks
for a specific shape and insists on it, with a repair loop, because a model
that returns prose where a schema was wanted is the normal case rather than
the exception.
"""

from shared.utils import CLOUD, LOCAL, NVIDIA, OPENROUTER

from .ModelService import ModelService
from .NvidiaModelService import NvidiaModelService
from .OpenRouterModelService import OpenRouterModelService
from .structured_generation import extract_json, generate_structured, schema_instruction

#: Which service answers for which source. It lives here rather than in
#: either module because it needs both, and putting it in the base one would
#: mean the base importing its own subclass.
_SERVICES = {
    LOCAL: ModelService,
    CLOUD: ModelService,
    NVIDIA: NvidiaModelService,
    OPENROUTER: OpenRouterModelService,
}


def for_source(source: str) -> ModelService:
    """The service for *source*, defaulting to a local Ollama host.

    Callers hold a qualified id and want to ask its own host a question --
    "can this embed?" -- without knowing which kind of host that is.
    """
    service = _SERVICES.get(source, ModelService)

    # Only the Ollama service distinguishes hosts; the vendor ones are
    # their own source and take no argument.
    return service(source=source) if service is ModelService else service()


__all__ = [
    "ModelService",
    "NvidiaModelService",
    "OpenRouterModelService",
    "extract_json",
    "for_source",
    "generate_structured",
    "schema_instruction",
]
