"""What each model is offered for, and which of a vendor's models are listed.

The rule under test is that the two lists are disjoint *by capability*, not by
what a model tolerates. Ollama embeds with any model — llama3.1:8b answers an
embed probe with 4096 dimensions — so "it answered" is not evidence that a
model belongs in the embedding list, and for a long time the picker offered
nomic-embed-text for chat and llama3.1:8b for embedding on exactly that basis.
"""

import httpx
import pytest

from controllers import ModelController
from controllers.ModelController import (
    COMPLETION,
    EMBEDDING,
    NvidiaModelController,
    _can,
)
from utils import LOCAL


# --- the predicate ------------------------------------------------------------


@pytest.mark.parametrize("capabilities, chat, embed", [
    (["embedding"], False, True),
    (["completion", "tools"], True, False),
    (["completion", "embedding"], True, True),
    # Unknown: every model can be asked to generate, and guessing that
    # something embeds is the error that costs a rebuilt index.
    (None, True, False),
    # An empty list is "the server said nothing", not "it can do nothing" —
    # treating it as the latter drops the model from both lists silently.
    ([], True, False),
])
def test_can(capabilities, chat, embed):
    model = {"capabilities": capabilities}

    assert _can(model, COMPLETION) is chat
    assert _can(model, EMBEDDING) is embed


# --- the split ----------------------------------------------------------------


@pytest.fixture
def catalogue_of(monkeypatch):
    """A catalogue built from one fake host, with no network anywhere."""

    async def build(models, widths):
        host = ModelController(source=LOCAL)

        async def listing():
            return models

        async def probe(_):
            return widths

        monkeypatch.setattr(host, "list_models", listing)
        monkeypatch.setattr(host, "_probe_all", probe)
        monkeypatch.setattr(ModelController, "_hosts", lambda self: [host])

        return await ModelController().catalogue()

    return build


def _model(tag, capabilities):
    return {
        "id": f"local/{tag}",
        "tag": tag,
        "source": LOCAL,
        "capabilities": capabilities,
    }


async def test_an_embedding_model_is_not_offered_for_chat(catalogue_of):
    catalogue = await catalogue_of([_model("nomic-embed-text", ["embedding"])], [768])

    assert [m["id"] for m in catalogue["chat"]] == []
    assert [m["id"] for m in catalogue["embedding"]] == ["local/nomic-embed-text"]


async def test_a_chat_model_that_can_embed_is_not_offered_for_embedding(catalogue_of):
    """The regression this whole change exists for: Ollama returns a vector
    for llama3.1:8b, and that used to be enough to list it."""
    catalogue = await catalogue_of([_model("llama3.1:8b", ["completion", "tools"])], [4096])

    assert [m["id"] for m in catalogue["chat"]] == ["local/llama3.1:8b"]
    assert catalogue["embedding"] == []


async def test_an_unknown_model_is_offered_for_chat_only(catalogue_of):
    catalogue = await catalogue_of([_model("mystery:1b", None)], [None])

    assert [m["id"] for m in catalogue["chat"]] == ["local/mystery:1b"]
    assert catalogue["embedding"] == []


async def test_the_two_lists_never_overlap(catalogue_of):
    catalogue = await catalogue_of(
        [
            _model("nomic-embed-text", ["embedding"]),
            _model("gemma4:e4b", ["completion", "vision"]),
            _model("llama3.1:8b", ["completion", "tools"]),
        ],
        [768, None, 4096],
    )

    chat = {m["id"] for m in catalogue["chat"]}
    embedding = {m["id"] for m in catalogue["embedding"]}

    assert not chat & embedding
    assert chat == {"local/gemma4:e4b", "local/llama3.1:8b"}
    assert embedding == {"local/nomic-embed-text"}


async def test_an_embedding_model_with_no_width_is_offered_for_neither(catalogue_of):
    """It says it embeds but would not; listing it would let a notebook
    rebuild its index against a model that returns nothing."""
    catalogue = await catalogue_of([_model("broken-embed", ["embedding"])], [None])

    assert catalogue["chat"] == []
    assert catalogue["embedding"] == []


# --- NVIDIA: what this account may actually call ------------------------------
#
# /v1/models publishes NVIDIA's whole catalogue — 82 ids — while a key may call
# a handful; the rest answer 404 "Function ...: Not found for account" only
# once a real request is made. Listing them all is what produced a 404 the
# moment one was chosen.


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


class _Client:
    """Answers each model with a canned status, or raises for a timeout."""

    def __init__(self, statuses):
        self.statuses = statuses
        self.asked: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, headers=None, json=None):
        model = json["model"]
        self.asked.append(model)

        status = self.statuses.get(model, 404)

        if status == "timeout":
            raise TimeoutError("no answer in time")

        return _Response(status)


@pytest.fixture
def nvidia(monkeypatch, settings):
    """An NVIDIA controller whose HTTP is canned, with a clean probe cache."""

    def build(statuses):
        client = _Client(statuses)
        # The module does `import httpx` and calls httpx.AsyncClient(...), so
        # patching the attribute on httpx itself is what it will pick up.
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: client)
        monkeypatch.setattr(
            NvidiaModelController, "_host_url", lambda self, source: "https://nvidia.invalid/v1"
        )
        controller = NvidiaModelController()
        controller._access_cache.clear()
        return controller, client

    return build


def _nv(tag):
    return {"id": f"nvidia/{tag}", "tag": tag, "source": "nvidia", "capabilities": None}


@pytest.mark.parametrize("status, kept", [
    # The body we send is invalid for every model, so 400 means "your request
    # is wrong" — which is only reachable once entitlement has passed.
    (400, True),
    (404, False),
    (200, True),
    ("timeout", False),
])
async def test_the_access_probe_reads_the_status(nvidia, status, kept):
    controller, _ = nvidia({"meta/x": status})

    usable = await controller._only_callable([_nv("meta/x")])

    assert bool(usable) is kept


async def test_only_callable_models_survive(nvidia):
    controller, _ = nvidia({"meta/ok": 400, "vendor/nope": 404, "vendor/hangs": "timeout"})

    usable = await controller._only_callable(
        [_nv("meta/ok"), _nv("vendor/nope"), _nv("vendor/hangs")]
    )

    assert [m["tag"] for m in usable] == ["meta/ok"]


async def test_the_verdict_is_cached_across_controllers(nvidia):
    """The routes build a controller per request; without the class-level
    cache the whole catalogue would be re-probed on every page load."""
    controller, client = nvidia({"meta/ok": 400})

    await controller._only_callable([_nv("meta/ok")])
    await NvidiaModelController()._only_callable([_nv("meta/ok")])

    assert client.asked == ["meta/ok"]


async def test_forget_probes_clears_the_access_cache(nvidia):
    controller, client = nvidia({"meta/ok": 400})

    await controller._only_callable([_nv("meta/ok")])
    ModelController.forget_probes()
    await controller._only_callable([_nv("meta/ok")])

    assert client.asked == ["meta/ok", "meta/ok"]


# --- NVIDIA: which endpoint decides ------------------------------------------


async def test_an_embedding_nim_is_classified_by_embedding_not_chat(nvidia, monkeypatch):
    """An embedding NIM refuses /chat/completions, so probing everything for
    chat dropped every embedding model — including the configured one."""
    controller, client = nvidia({})

    async def width(tag):
        return 2048 if "embed" in tag else None

    monkeypatch.setattr(controller, "embedding_dimensions", width)

    usable = await controller._only_usable([_nv("nvidia/nemotron-3-embed-1b")])

    assert [m["tag"] for m in usable] == ["nvidia/nemotron-3-embed-1b"]
    assert usable[0]["capabilities"] == [EMBEDDING]
    # Never offered the chat endpoint at all.
    assert client.asked == []


async def test_a_model_named_embed_that_cannot_falls_back_to_the_chat_probe(
    nvidia, monkeypatch
):
    """The name picks which endpoint to try first; the endpoint answers."""
    controller, client = nvidia({"vendor/embed-but-chats": 400})

    async def width(_):
        return None

    monkeypatch.setattr(controller, "embedding_dimensions", width)

    usable = await controller._only_usable([_nv("vendor/embed-but-chats")])

    assert [m["tag"] for m in usable] == ["vendor/embed-but-chats"]
    assert usable[0]["capabilities"] is None      # chat, by elimination
    assert client.asked == ["vendor/embed-but-chats"]


# --- the size a tag advertises ------------------------------------------------
#
# NVIDIA publishes no parameter count, but nearly every tag carries one, and
# the picker groups by size. Ollama reports its own, so only this side needs
# reading out of the name.


@pytest.mark.parametrize("tag, parameters", [
    ("meta/llama-3.2-11b-vision-instruct", "11B"),
    ("openai/gpt-oss-120b", "120B"),
    ("nvidia/riva-translate-4b-instruct-v2", "4B"),
    # A mixture-of-experts tag names its total before its active count; the
    # total is the number worth showing.
    ("nvidia/nemotron-3-super-120b-a12b", "120B"),
    ("google/diffusiongemma-26b-a4b-it", "26B"),
    # The version is not a size: "llama-3.2" and "1.5" must not be read as one.
    ("nvidia/ising-calibration-1.5-31b", "31B"),
    # Nothing advertised — reported as unknown rather than guessed.
    ("minimaxai/minimax-m3", None),
    ("nvidia/nemotron-parse", None),
    ("poolside/laguna-xs-2.1", None),
])
def test_parameters_are_read_from_the_tag(tag, parameters):
    assert NvidiaModelController._parameters_of(tag) == parameters
