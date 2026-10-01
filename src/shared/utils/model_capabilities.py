"""What a model can do, and what its tag says about it.

Sibling to :mod:`utils.model_ids`, which reads *where* a model lives out of its
id. This reads the other half: whether it is offered for chat or for embedding,
and the few facts a vendor encodes in the tag instead of publishing — a
parameter count, a name that suggests an embedder, a guard model that is
neither.

These are pure functions over a listing entry or a bare tag, with no host, no
client and no settings behind them. They lived on ModelService and its
NVIDIA subclass, which meant a caller wanting to know whether a tag looks like
an embedding model had to instantiate a controller — and, because half of them
were classmethods on the base and half on the subclass, had to know which one.
"""

import re

from shared.enums import ModelCapability, NvidiaSafetyModelMarker

# Ollama's capability names, as /api/show reports them. A model may hold
# several ("completion", "tools", "vision", "thinking"); only these two decide
# which list it belongs in.
COMPLETION = ModelCapability.COMPLETION.value
EMBEDDING = ModelCapability.EMBEDDING.value

# "llama-3.2-11b-vision", "nemotron-3-super-120b-a12b", "gpt-oss-120b".
# NVIDIA publishes no parameter count, but nearly every tag carries one.
# Anchored on a digit run followed by "b" at a token boundary, so the
# version in "llama-3.2" is not read as a size, and the first match wins:
# a mixture-of-experts tag names its total before its active count
# ("120b-a12b" is a 120B model), and the total is the useful number.
_PARAMETERS = re.compile(r"(?:^|[-_/])(\d+(?:\.\d+)?)b(?=$|[-_/])")

# Substrings that make a model worth an embed probe. A heuristic on the
# *candidate set* only: the answer still comes from the endpoint.
_EMBEDDING_HINTS = ("embed", "retriev")

# Safety classifiers and guardrails are not general chat or embedding models
# for this application, so they are kept out of the catalogue entirely.
_SAFETY_MODEL_MARKERS = tuple(marker.value for marker in NvidiaSafetyModelMarker)


def can(model: dict, capability: str) -> bool:
    """Whether *model* is offered for *capability*.

    Unknown capabilities (an older Ollama, a vendor that publishes none) count
    as completion and nothing else: every model can be asked to generate, and
    guessing that something embeds is the error that costs a rebuilt index.

    An *empty* list is treated the same as a missing one. "The server told us
    nothing" is not evidence a model can do nothing, and the alternative is a
    model that silently appears in neither list — which is the failure this
    function exists to end.
    """
    capabilities = model.get("capabilities")

    if not capabilities:
        return capability == COMPLETION

    return capability in capabilities


def parameters_of(tag: str) -> str | None:
    """The parameter count a tag advertises, in Ollama's spelling.

    Returned as "11B" rather than a number so the picker can treat every
    source's value the same way — Ollama reports "8.0B" from the tag list,
    and a model that names no size (minimax-m3, nemotron-parse) reports
    nothing here rather than a guess.
    """
    match = _PARAMETERS.search(tag.lower())

    return f"{match.group(1)}B".upper() if match else None


def looks_like_embedding(tag: str) -> bool:
    """Whether *tag* is worth spending an embed probe on.

    A hint about which endpoint to try first, never a verdict: a "…embed…"
    model that does not embed is still excluded, because the endpoint answers
    and the name does not.
    """
    return any(hint in tag.lower() for hint in _EMBEDDING_HINTS)


def is_safety_model(tag: str) -> bool:
    """Whether *tag* names a safety classifier or guard model."""
    normalized = tag.lower().replace("/", "-")

    return any(marker in normalized for marker in _SAFETY_MODEL_MARKERS)
