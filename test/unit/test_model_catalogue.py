"""What each model is offered for, and which of a vendor's models are listed.

The rule under test is that the two lists are disjoint *by capability*, not by
what a model tolerates. Ollama embeds with any model — llama3.1:8b answers an
embed probe with 4096 dimensions — so "it answered" is not evidence that a
model belongs in the embedding list, and for a long time the picker offered
nomic-embed-text for chat and llama3.1:8b for embedding on exactly that basis.
"""

import asyncio
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
        # These tests exercise the fake Ollama catalogue only. Do not let
        # optional developer credentials from src/.env add hosted models to
        # exact-list assertions.
        monkeypatch.setattr(host.settings, "ANTHROPIC_API_KEY", None)
        monkeypatch.setattr(host.settings, "GOOGLE_API_KEY", None)

        async def listing():
            return models

        async def probe(_):
            return widths

        monkeypatch.setattr(host, "list_models", listing)
        monkeypatch.setattr(host, "_probe_all", probe)
        # _hosts now takes an optional source filter, so the stand-in has to
        # accept it too — the fixture drives the real catalogue() code path.
        monkeypatch.setattr(ModelController, "_hosts", lambda self, sources=None: [host])

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
    """Answers per model *and per stage*.

    The probe asks twice: an empty `messages` list to test entitlement, then a
    real one-token request to test that the model accepts the shape this
    application sends. `statuses` maps a tag to (entitlement, usability); a
    value of "timeout" raises instead of answering.
    """

    def __init__(self, statuses):
        self.statuses = statuses
        self.asked: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, headers=None, json=None):
        model = json["model"]
        stage = "entitlement" if not json.get("messages") else "usability"
        self.asked.append((model, stage))

        entitled, usable = self.statuses.get(model, (404, 404))
        status = entitled if stage == "entitlement" else usable

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


@pytest.mark.parametrize("statuses, kept, why", [
    # Entitlement passes (400 = "your body is wrong", reachable) and the real
    # request is accepted.
    ((400, 200), True, "callable and accepts our request"),
    # Not this account's model — the free pass alone settles it.
    ((404, 200), False, "not entitled"),
    # Entitled, but rejects the shape we send: several NIM schemas forbid
    # unknown fields outright. This is the failure that reached a user as
    # "extra_forbidden ... max_completion_tokens" *after* choosing the model.
    ((400, 400), False, "rejects our request shape"),
    # Entitled, but not a chat model at all.
    ((400, 500), False, "not a chat model"),
    # Never answers — too slow to hold a conversation.
    ((400, "timeout"), False, "times out on a real request"),
    (("timeout", 200), False, "times out on entitlement"),
])
async def test_only_models_that_actually_answer_are_offered(nvidia, statuses, kept, why):
    controller, _ = nvidia({"meta/x": statuses})

    usable = await controller._only_callable([_nv("meta/x")])

    assert bool(usable) is kept, why


async def test_an_unentitled_model_costs_only_the_free_probe(nvidia):
    """The real request runs inference; it must not be spent on a model the
    account cannot call anyway."""
    controller, client = nvidia({"vendor/nope": (404, 200)})

    await controller._only_callable([_nv("vendor/nope")])

    assert [stage for _, stage in client.asked] == ["entitlement"]


async def test_the_probe_sends_the_field_the_provider_sends(nvidia, monkeypatch):
    """A probe testing a shape the provider no longer uses would pass models
    that then fail on first use — which is exactly what happened."""
    from factories.llmchatting import NvidiaChatProvider

    sent = {}

    controller, client = nvidia({"meta/x": (400, 200)})

    original = client.post

    async def capture(url, headers=None, json=None):
        if json.get("messages"):
            sent.update(json)
        return await original(url, headers=headers, json=json)

    client.post = capture

    await controller._only_callable([_nv("meta/x")])

    assert NvidiaChatProvider._MAX_TOKENS_FIELD in sent
    assert sent[NvidiaChatProvider._MAX_TOKENS_FIELD] == 1


async def test_only_working_models_survive(nvidia):
    controller, _ = nvidia({
        "meta/ok": (400, 200),
        "vendor/nope": (404, 404),
        "vendor/wrong-shape": (400, 400),
        "vendor/hangs": (400, "timeout"),
    })

    usable = await controller._only_callable([
        _nv("meta/ok"), _nv("vendor/nope"), _nv("vendor/wrong-shape"), _nv("vendor/hangs")
    ])

    assert [m["tag"] for m in usable] == ["meta/ok"]


async def test_the_verdict_is_cached_across_controllers(nvidia):
    """The routes build a controller per request; without the class-level
    cache the whole catalogue would be re-probed on every page load."""
    controller, client = nvidia({"meta/ok": (400, 200)})

    await controller._only_callable([_nv("meta/ok")])
    await NvidiaModelController()._only_callable([_nv("meta/ok")])

    assert [tag for tag, _ in client.asked] == ["meta/ok", "meta/ok"]  # both stages, once


async def test_a_timeout_is_not_remembered_as_a_no(nvidia):
    """A large model can miss the deadline waking up and answer comfortably
    once warm. Caching that as a refusal would hide it until a restart."""
    controller, client = nvidia({"meta/slow": (400, "timeout")})

    await controller._only_callable([_nv("meta/slow")])
    assert "nvidia/meta/slow" not in controller._access_cache

    controller.statuses = None
    client.statuses["meta/slow"] = (400, 200)

    assert await controller._only_callable([_nv("meta/slow")])


async def test_forget_probes_clears_the_access_cache(nvidia):
    controller, client = nvidia({"meta/ok": (400, 200)})

    await controller._only_callable([_nv("meta/ok")])
    ModelController.forget_probes()
    await controller._only_callable([_nv("meta/ok")])

    assert len(client.asked) == 4      # both stages, twice


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
    controller, client = nvidia({"vendor/embed-but-chats": (400, 200)})

    async def width(_):
        return None

    monkeypatch.setattr(controller, "embedding_dimensions", width)

    usable = await controller._only_usable([_nv("vendor/embed-but-chats")])

    assert [m["tag"] for m in usable] == ["vendor/embed-but-chats"]
    assert usable[0]["capabilities"] is None      # chat, by elimination
    assert [tag for tag, _ in client.asked] == ["vendor/embed-but-chats"] * 2


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


# --- configured hosted models: listed is not the same as callable -------------
#
# A key in .env proves only that a key was typed. Gemini 2.5 answers 404 "no
# longer available to new users", an Anthropic key with no credit answers 400,
# and both used to reach the user as a failed generation *after* they had
# chosen the model and asked a question.


class _FakeChat:
    """A chat client that fails however the test says, and records closing."""

    def __init__(self, error=None, delay=0):
        self.error = error
        self.delay = delay
        self.closed = False
        self.max_tokens = None

    async def generate_text(self, prompt, max_tokens=None, **kwargs):
        self.max_tokens = max_tokens

        if self.delay:
            await asyncio.sleep(self.delay)

        if self.error:
            raise self.error

        return "hi"

    async def aclose(self):
        self.closed = True


@pytest.fixture
def configured(monkeypatch):
    """A controller with both hosted keys set and no host discovery."""
    ModelController.forget_probes()
    ModelController._probes_in_flight.clear()

    controller = ModelController(source=LOCAL)
    monkeypatch.setattr(controller.settings, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(controller.settings, "ANTHROPIC_MODEL_ID", "claude-x")
    monkeypatch.setattr(controller.settings, "GOOGLE_API_KEY", None)
    monkeypatch.setattr(controller, "_hosts", lambda sources=None: [])

    def install(client):
        monkeypatch.setattr(
            "factories.provider_cache.ProviderCache.chatting",
            lambda self, model_id=None: client,
        )
        return client

    yield controller, install

    ModelController.forget_probes()
    ModelController._probes_in_flight.clear()


@pytest.mark.parametrize("message, reason", [
    ("Your credit balance is too low to access the Anthropic API.", "No API credit"),
    ("This model models/gemini-2.5-flash is no longer available to new users.",
     "Retired by the vendor"),
    ("anthropic-workspace-id is required when authenticating", "Workspace id required"),
    ("API key not valid. Please pass a valid API key.", "API key rejected"),
    ("You exceeded your current quota, please check your plan", "Quota exceeded"),
])
def test_a_refusal_is_reduced_to_a_phrase_that_fits_a_row(message, reason):
    """The vendor sentences are long and their wording changes; the picker has
    room for a few words."""
    assert ModelController._unavailable_reason(Exception(message)) == reason


def test_an_unrecognised_refusal_keeps_the_vendors_own_words():
    """Better a trimmed sentence the user can search for than a category
    invented to cover something nobody has seen before."""
    reason = ModelController._unavailable_reason(Exception("Teapot mode engaged. Retry."))

    assert reason == "Teapot mode engaged"


def test_running_out_of_budget_counts_as_reaching_the_model():
    """The probe asks whether a model takes the request, not what it says. On
    a thinking model a budget too small to answer with is the *fast* path —
    about a second, against thirty for one big enough to produce text."""
    truncated = Exception(
        "Google returned no text (model='gemini-3.6-flash', "
        "finish_reason=<FinishReason.MAX_TOKENS: 'MAX_TOKENS'>)"
    )

    assert ModelController._reached_generation(truncated)


def test_a_complaint_about_the_max_tokens_field_is_not_a_pass():
    """That request never reached the model, so it says nothing about access.
    Both halves of the test matter, which is why finish_reason is required."""
    rejected = Exception("400 invalid_request_error: max_tokens: must be >= 1")

    assert not ModelController._reached_generation(rejected)


async def test_a_refused_model_is_marked_rather_than_dropped(configured):
    """The opposite of the NVIDIA path. There, dropping unusable models from a
    catalogue of eighty is a kindness; here there are two, both named in .env,
    so one that vanished would read as the bug this prevents — "Anthropic is
    missing despite my API key"."""
    controller, install = configured
    client = install(_FakeChat(Exception("Your credit balance is too low")))

    await controller._probe_configured("anthropic/claude-x")

    catalogue = await controller.catalogue(probe_embeddings=False)
    listed = [m for m in catalogue["chat"] if m["source"] == "anthropic"]

    assert len(listed) == 1
    assert listed[0]["available"] is False
    assert listed[0]["unavailable_reason"] == "No API credit"
    assert client.closed


async def test_the_catalogue_does_not_wait_for_the_probe(configured):
    """One probe measured against the real vendors takes 0.2 to 30 seconds.
    Blocking on it would stall the very list the user is waiting for."""
    controller, install = configured
    install(_FakeChat(delay=30))

    catalogue = await asyncio.wait_for(
        controller.catalogue(probe_embeddings=False), timeout=1
    )

    listed = [m for m in catalogue["chat"] if m["source"] == "anthropic"][0]

    # Three-valued on purpose: "nobody has asked yet" is not the same claim as
    # "your account cannot call this", and must not be painted like it.
    assert listed["available"] is None
    assert controller._probes_in_flight

    for task in list(controller._probes_in_flight.values()):
        task.cancel()


async def test_a_rate_limited_probe_records_no_verdict(configured):
    """The probe spends the same quota it reports on. Caching a 429 would let
    the check condemn a working model on evidence it manufactured."""
    controller, install = configured
    install(_FakeChat(Exception("Error code: 429 - Too Many Requests")))

    await controller._probe_configured("google/gemini-x")

    assert "google/gemini-x" not in controller._configured_cache
    assert controller._probe_cooldown["google/gemini-x"] > 0


async def test_a_slow_probe_is_not_remembered_as_a_no(configured):
    """Same reasoning as the NVIDIA timeout: a slow vendor is not a broken
    one, and Gemini can take half a minute over a request it then answers."""
    controller, install = configured
    monkey = _FakeChat(delay=5)
    install(monkey)
    controller._CONFIGURED_PROBE_TIMEOUT = 0.05

    await controller._probe_configured("google/gemini-x")

    assert "google/gemini-x" not in controller._configured_cache


async def test_the_probe_is_not_repeated_while_one_is_in_flight(configured):
    """Two catalogue calls in the same second must not both pay for it."""
    controller, install = configured
    install(_FakeChat(delay=30))

    controller._schedule_verification([{"id": "anthropic/claude-x"}])
    first = dict(controller._probes_in_flight)
    controller._schedule_verification([{"id": "anthropic/claude-x"}])

    assert controller._probes_in_flight == first

    for task in list(controller._probes_in_flight.values()):
        task.cancel()


async def test_a_settled_verdict_is_not_probed_again(configured):
    """Reading the cache is free; filling it is not."""
    controller, install = configured
    install(_FakeChat(Exception("Your credit balance is too low")))

    await controller._probe_configured("anthropic/claude-x")

    model = {"id": "anthropic/claude-x"}

    assert controller._apply_verdict(model) is False
    assert model["available"] is False


async def test_forget_probes_clears_the_configured_verdicts(configured):
    """A newly funded account must not stay greyed out until a restart."""
    controller, install = configured
    install(_FakeChat(Exception("Your credit balance is too low")))

    await controller._probe_configured("anthropic/claude-x")
    assert controller._configured_cache

    ModelController.forget_probes()

    assert not controller._configured_cache
    assert not controller._probe_cooldown
