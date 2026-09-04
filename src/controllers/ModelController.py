"""Discovers which models are available, and what each can do.

Nothing here is hardcoded to a model name. Whether a model can embed is
determined by asking it to embed, because the tag list does not say — and the
answer also tells us the vector width, which the vector store needs.

There may be two Ollama hosts: the local one, and a second reachable over the
network (OLLAMA_CLOUD_BASE_URL). Both are Ollama, so a model is identified by
which host it lives on plus its tag — "local/llama3.1:8b",
"cloud/gemma4:latest". The same tag can exist on both and they are different
models to us.

A hosted vendor is a third source of the same shape: NVIDIA's catalogue arrives
as "nvidia/meta/llama-3.2-11b-vision-instruct". One class per source, each
answering the same two questions — what do you have, and can this one embed —
so `catalogue` merges them without knowing which is which.
"""

import asyncio
import re
import time

import httpx  # ty: ignore[unresolved-import]

from enums import ModelCapability, NvidiaSafetyModelMarker
from exceptions import LLMProviderError
from utils import (
    ANTHROPIC,
    CLOUD,
    GOOGLE,
    LOCAL,
    NVIDIA,
    default_chat_model,
    default_embedding_model,
    host_for,
    qualify,
)

from .BaseController import BaseController

# Ollama's capability names, as /api/show reports them. A model may hold
# several ("completion", "tools", "vision", "thinking"); only these two decide
# which list it belongs in.
COMPLETION = ModelCapability.COMPLETION.value
EMBEDDING = ModelCapability.EMBEDDING.value


def _can(model: dict, capability: str) -> bool:
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


class ModelController(BaseController):
    """Lists installed Ollama models and probes their capabilities."""

    # qualified model id -> dimensions, or None when it cannot embed.
    #
    # Keyed by the *qualified* id: the same tag on two hosts is two models,
    # and one of them answering an embed probe says nothing about the other.
    #
    # Deliberately a *class* attribute. Probing means asking the model to embed
    # something, which costs a real inference call — around five seconds for a
    # 8B embedding model. The routes build a fresh ModelController per request,
    # so an instance-level cache was thrown away every time and the catalogue
    # re-probed all seven models on every call: eighteen seconds, during which
    # the settings dropdowns sit empty. Sharing it across instances makes the
    # first call slow and every later one instant.
    _embedding_cache: dict[str, int | None] = {}

    # qualified model id -> why it cannot be used, or None when it answered.
    #
    # A class attribute for the same reason as _embedding_cache: the routes
    # build a fresh controller per request, so an instance cache would re-probe
    # every vendor on every catalogue call.
    _configured_cache: dict[str, str | None] = {}

    # Deliberately too small to answer with. The probe asks "will this model
    # take my request", not "what does it say", and on a thinking model those
    # have very different prices: Gemini 3.x reasons before it writes, so a
    # budget big enough for real text costs 30 seconds, while one too small
    # comes back in about a second having proved everything that matters —
    # the key authenticated, the model exists, the account may call it, and
    # generation started. Truncation *is* the pass; see _reached_generation.
    _CONFIGURED_PROBE_MAX_TOKENS = 16

    _CONFIGURED_PROBE_TIMEOUT = 10

    # How long to leave a model alone after a probe that settled nothing.
    _PROBE_COOLDOWN = 300

    # Model ids whose probe has not come back yet, so two catalogue calls in
    # the same second do not both pay for one.
    _probes_in_flight: dict[str, "asyncio.Task"] = {}

    # Model id -> monotonic time before which not to probe again.
    _probe_cooldown: dict[str, float] = {}

    # Matched against the provider's error text, longest-lived cause first. The
    # vendor sentences are long and change wording; the picker needs a phrase
    # short enough to sit in a row.
    _UNAVAILABLE_REASONS = (
        ("credit balance", "No API credit"),
        ("no longer available", "Retired by the vendor"),
        ("quota", "Quota exceeded"),
        ("workspace", "Workspace id required"),
        ("api key", "API key rejected"),
        ("unauthenticated", "API key rejected"),
        ("permission", "Key not permitted"),
        ("not found", "Not available to this key"),
    )

    def __init__(self, base_url: str | None = None, source: str = LOCAL) -> None:

        super().__init__()

        self.source = source
        self.base_url = (base_url or self._host_url(source)).rstrip("/")

    def _host_url(self, source: str) -> str:
        """Where this source answers. Overridden per vendor."""
        return host_for(self.settings, source)

    async def list_models(self) -> list[dict]:
        """Every model this host has pulled, ids qualified by source."""

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()

        except Exception as exc:
            raise LLMProviderError(
                f"Could not list Ollama models at {self.base_url}: {exc} " "(is `ollama serve` running?)"
            ) from exc

        models = []

        for entry in payload.get("models", []):
            details = entry.get("details") or {}
            models.append(
                {
                    "id": qualify(self.source, entry["name"]),
                    "tag": entry["name"],
                    "source": self.source,
                    "size_gb": round(entry.get("size", 0) / 1e9, 2),
                    "family": details.get("family"),
                    "parameters": details.get("parameter_size"),
                    # Newer Ollama reports what a model can do. Older builds
                    # omit it entirely, which is not the same as "nothing" —
                    # None means "unknown, go and ask".
                    "capabilities": entry.get("capabilities"),
                }
            )

        models.sort(key=lambda m: m["id"])

        # /api/tags does not carry capabilities on every Ollama build — on the
        # version this was written against it is null for every entry, which
        # is why the catalogue used to offer nomic-embed-text as a chat model
        # and llama3.1:8b as an embedding one. /api/show does carry it.
        await self._fill_capabilities(models)

        return models

    async def _fill_capabilities(self, models: list[dict]) -> None:
        """Ask /api/show what each model can do, for the ones that did not say.

        Concurrent, unlike the embedding probe: /api/show reads a manifest and
        returns, where /api/embed loads the model into memory. There is
        nothing here for two requests to contend over.
        """
        unknown = [m for m in models if m.get("capabilities") is None]

        if not unknown:
            return

        async with httpx.AsyncClient(timeout=15) as client:

            async def ask(model: dict) -> None:
                try:
                    response = await client.post(f"{self.base_url}/api/show", json={"model": model["tag"]})
                    if response.status_code == 200:
                        model["capabilities"] = response.json().get("capabilities")

                except Exception as exc:
                    # Still None afterwards, which reads as "unknown" — and an
                    # unknown model is offered for chat rather than hidden.
                    self.logger.debug("Could not read capabilities for %r: %s", model["id"], exc)

            await asyncio.gather(*(ask(model) for model in unknown))

    async def _probe_all(self, models: list[dict]) -> list[dict]:
        """Widths for this host's models, in order, one at a time.

        Serial on purpose. A probe makes Ollama load the model, so firing all
        of them at once at a single host makes it swap several multi-billion
        parameter models in and out against each other — slower than simply
        asking one after another. Hosts are probed in parallel with each
        other; it is only *within* one host that the queue matters.
        """
        widths = []

        for model in models:
            capabilities = model.get("capabilities")

            # A host that says what its models do is worth believing: probing
            # a chat-only model just waits for a refusal, and over a network
            # that wait is the slowest thing in the whole call.
            if capabilities is not None and "embedding" not in capabilities:
                widths.append(None)
                continue

            widths.append(await self.embedding_dimensions(model["tag"]))

        return widths

    async def _safe_list(self) -> list[dict]:
        """list_models, except a host that is down contributes nothing.

        The second host may be a tunnel, and tunnels go away. One unreachable
        host must not empty the whole picker — the models you do have are
        still usable.
        """
        try:
            return await self.list_models()

        except LLMProviderError as exc:
            self.logger.warning("Skipping %s Ollama at %s: %s", self.source, self.base_url, exc)
            return []

    async def embedding_dimensions(self, tag: str) -> int | None:
        """Vector width for *tag* on this host, or None if it cannot embed.

        Takes the bare tag — the host is this controller's own. The only
        reliable test is to try: Ollama answers "this model does not support
        embeddings" for generation-only models, and there is no flag on the
        tag list that predicts it.
        """
        model_id = qualify(self.source, tag)

        if model_id in self._embedding_cache:
            return self._embedding_cache[model_id]

        dimensions: int | None = None

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": tag, "input": ["probe"]},
                )

                if response.status_code == 200:
                    vectors = response.json().get("embeddings") or []
                    if vectors:
                        dimensions = len(vectors[0])

        except Exception as exc:
            # A probe failure is not fatal — it just means "unknown", and the
            # model stays out of the embedding list.
            self.logger.debug("Embedding probe failed for %r: %s", model_id, exc)

        self._embedding_cache[model_id] = dimensions

        return dimensions

    @classmethod
    def forget_probes(cls) -> None:
        """Drop every probe cache, so newly pulled models are picked up.

        Reaches the vendor caches too — they live on subclasses, and a test
        that clears only this one leaks an access verdict into the next.
        """
        cls._embedding_cache.clear()
        cls._configured_cache.clear()
        cls._probe_cooldown.clear()

        for subclass in cls.__subclasses__():
            cache = getattr(subclass, "_access_cache", None)
            if cache is not None:
                cache.clear()

    def _hosts(self, sources: list[str] | None = None) -> list["ModelController"]:
        """Every source we are configured to talk to.

        A source that is not configured is left out rather than listed and
        broken: no OLLAMA_CLOUD_BASE_URL means no cloud models in the picker,
        no NVIDIA_API_KEY means no NVIDIA ones.
        """
        hosts: list[ModelController] = [ModelController(source=LOCAL)]

        if self.settings.OLLAMA_CLOUD_BASE_URL:
            hosts.append(ModelController(source=CLOUD))

        if self.settings.NVIDIA_API_KEY:
            hosts.append(for_source(NVIDIA))

        if sources is not None:
            hosts = [host for host in hosts if host.source in sources]

        return hosts

    def configured_chat_models(self) -> list[dict]:
        """Configured hosted chat models, without any network discovery.

        Carries a cached usability verdict when one exists, so the instant
        slice of the picker is not only fast but honest about a model already
        known to be uncallable. Nothing here probes: an unknown model reports
        ``available: None`` and :meth:`catalogue` is what goes and asks.
        """
        models = []
        configured = (
            (ANTHROPIC, self.settings.ANTHROPIC_API_KEY, self.settings.ANTHROPIC_MODEL_ID, "Anthropic"),
            (GOOGLE, self.settings.GOOGLE_API_KEY, self.settings.GOOGLE_MODEL_ID, "Google"),
        )
        for source, api_key, model_id, family in configured:
            if not api_key:
                continue
            models.append(
                {
                    "id": qualify(source, model_id),
                    "tag": model_id,
                    "source": source,
                    "size_gb": None,
                    "family": family,
                    "parameters": None,
                    "capabilities": [COMPLETION],
                }
            )
            self._apply_verdict(models[-1])

        return models

    @staticmethod
    def _rate_limited(exc: Exception) -> bool:
        """Whether the vendor refused because the account is over its rate."""
        text = str(exc).lower()

        return "429" in text or "too many requests" in text or "rate limit" in text

    @staticmethod
    def _reached_generation(exc: Exception) -> bool:
        """Whether this failure happened *after* the model accepted the call.

        A model that ran out of output budget got further than any check here
        cares about, so the truncation counts as a pass. Both halves are
        required: "finish_reason" pins it to a response the vendor actually
        produced, so a 400 complaining about the max_tokens *field* — a request
        that never reached the model — is not mistaken for one.
        """
        text = str(exc).lower()

        return "finish_reason" in text and "max_tokens" in text

    @classmethod
    def _unavailable_reason(cls, exc: Exception) -> str:
        """A row-sized phrase for why a vendor refused the probe."""
        text = str(exc).lower()

        for needle, reason in cls._UNAVAILABLE_REASONS:
            if needle in text:
                return reason

        # Nothing recognised. Better to show the vendor's own first sentence,
        # trimmed, than to invent a category for it.
        first = str(exc).split(".")[0].strip()

        return (first[:77] + "...") if len(first) > 80 else (first or "Unavailable")

    def _apply_verdict(self, model: dict) -> bool:
        """Attach what is already known about *model*. True if a probe is due.

        ``available`` is deliberately three-valued. ``None`` means nobody has
        asked yet — which the picker must render differently from ``False``,
        because "we have not checked" and "your account cannot call this" are
        not the same claim to make about a model the user configured.
        """
        cached = self._configured_cache.get(model["id"], ...)

        if cached is not ...:
            model["available"] = cached is None
            model["unavailable_reason"] = cached
            return False

        model["available"] = None
        model["unavailable_reason"] = None

        return time.monotonic() >= self._probe_cooldown.get(model["id"], 0.0)

    def _schedule_verification(self, models: list[dict]) -> None:
        """Probe, in the background, whichever models have no verdict yet.

        Never awaited, and that is the whole point. A key in .env proves only
        that a key was typed: not that the model still exists (Gemini 2.5 now
        answers 404 "no longer available to new users"), that the account has
        credit, or that the key carries the header the vendor wants. All three
        used to reach the user as a failed generation *after* they had chosen
        the model and asked a question.

        But checking cannot sit in front of the picker. Measured against the
        real vendors, one probe takes anywhere from 0.2 to 30 seconds — the
        variance is Gemini's own, not the SDK's — and it spends live quota, so
        a probe on every catalogue call would both stall the list the user is
        waiting on and burn the allowance it is reporting. Instead the verdict
        lands in the cache and the next catalogue call reads it.

        Marked, not filtered — the opposite of the NVIDIA path. There the
        catalogue is eighty models the user never asked for and dropping the
        unusable ones is a kindness. Here there are two, both named explicitly
        in .env, so a model that silently disappeared would read as the bug
        this is meant to prevent: "Anthropic is missing despite my API key".
        """
        for model in models:
            if model["id"] in self._probes_in_flight:
                continue

            task = asyncio.create_task(self._probe_configured(model["id"]))

            # Held so the loop cannot garbage-collect a running task, and
            # cleared on completion so a later call can probe again.
            self._probes_in_flight[model["id"]] = task
            task.add_done_callback(lambda _, key=model["id"]: self._probes_in_flight.pop(key, None))

    async def _probe_configured(self, model_id: str) -> None:
        """Ask *model_id* for one token, and write down what happened."""
        from factories.provider_cache import ProviderCache

        client = None
        reason: str | None = None

        try:
            client = ProviderCache(self.settings).chatting(model_id)
            await asyncio.wait_for(
                client.generate_text("hi", max_tokens=self._CONFIGURED_PROBE_MAX_TOKENS),
                timeout=self._CONFIGURED_PROBE_TIMEOUT,
            )

        except (asyncio.TimeoutError, TimeoutError):
            # No verdict: a slow vendor is not a broken one, and Gemini can
            # take half a minute over a request it then answers correctly.
            self._probe_cooldown[model_id] = time.monotonic() + self._PROBE_COOLDOWN
            return

        except Exception as exc:
            if self._rate_limited(exc):
                # Also no verdict, and the most important one not to record.
                # A 429 says the account is busy, not that the model is
                # unusable — and since the probe itself consumes quota,
                # caching this would let the check condemn the model on
                # evidence it manufactured.
                self._probe_cooldown[model_id] = time.monotonic() + self._PROBE_COOLDOWN
                self.logger.debug("Probe for %r rate-limited; no verdict", model_id)
                return

            if not self._reached_generation(exc):
                reason = self._unavailable_reason(exc)
                self.logger.info("%s unusable: %s", model_id, str(exc)[:200])

        finally:
            if client is not None:
                await client.aclose()

        self._configured_cache[model_id] = reason

    async def catalogue(self, probe_embeddings: bool = True, sources: list[str] | None = None) -> dict:
        """Installed models split into what they can be used for.

        Spans every configured source regardless of which one this instance
        points at — the picker shows one list, and each entry says where it
        lives.

        The two lists are disjoint by capability, not by what a model happens
        to tolerate. Ollama will embed with *any* model — llama3.1:8b answers
        an embed probe with 4096 dimensions — so "it answered" was never
        evidence that a model belongs in the embedding list, and the picker
        offered chat models for embedding and embedding models for chat.
        `capabilities` decides instead; a model that reports neither is
        offered for chat, since that is the only thing every model can do.
        """
        hosts = self._hosts(sources)

        listings = await asyncio.gather(*(host._safe_list() for host in hosts))

        models = [model for listing in listings for model in listing]

        # Configured hosted models need no *discovery* call, so they belong to
        # whichever slice asked for them — and to the unfiltered catalogue.
        # They do need a usability probe, which is why they are collected
        # first rather than appended one by one.
        configured = [
            model
            for model in self.configured_chat_models()
            if (sources is None or model["source"] in sources)
            and not any(existing["id"] == model["id"] for existing in models)
        ]

        # Reading the cache is free; filling it is not, so the probe runs
        # detached and this call returns with whatever is already known.
        self._schedule_verification([model for model in configured if self._apply_verdict(model)])

        models.extend(configured)

        models.sort(key=lambda m: m["id"])

        embedding = []

        if probe_embeddings:
            by_host = [[m for m in models if m["source"] == host.source] for host in hosts]

            probed = await asyncio.gather(*(host._probe_all(mine) for host, mine in zip(hosts, by_host)))

            embedding = [
                {**model, "dimensions": width}
                for mine, widths in zip(by_host, probed)
                for model, width in zip(mine, widths)
                if width and _can(model, EMBEDDING)
            ]
            embedding.sort(key=lambda m: m["id"])

        return {
            "chat": [m for m in models if _can(m, COMPLETION)],
            "embedding": embedding,
            "current": {
                # The same helper the chat routes report through, so the id in
                # the catalogue and the id on a notebook are one string. They
                # were two for a while, and the picker called the difference
                # "Missing".
                "chat": default_chat_model(self.settings),
                "embedding": default_embedding_model(self.settings),
                "embedding_dimensions": self.settings.EMBEDDING_MODEL_SIZE,
            },
        }


class NvidiaModelController(ModelController):
    """NVIDIA's hosted catalogue, in the same shape as an Ollama host's.

    Two things differ from Ollama and neither is cosmetic.

    **The catalogue is not an entitlement list.** ``GET /v1/models`` returns
    every NIM NVIDIA publishes — dozens — while a given key may call only a
    handful; the rest answer ``404 Function ...: Not found for account ...``.
    They are still listed, because the alternative is a real call per model on
    every catalogue refresh, and a 404 at chat time names the model plainly.

    **Nothing says which models embed**, and probing all of them would be one
    request each. Ollama's probe is cheap enough to run over everything because
    a host holds a handful of tags; here the list is long and remote, so only
    the plausible ones are asked — and *asked*, not assumed, so a name that
    looks like an embedding model but cannot embed is still excluded.
    """

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

    # qualified id -> True when this account may call the model. Beside
    # _embedding_cache, on the class and for the same reason: the routes build
    # a controller per request, and this is the answer to a network round trip.
    _access_cache: dict[str, bool] = {}

    # How many access probes are in flight at once. They are cheap (no
    # inference) but there are eighty of them, and a vendor that sees eighty
    # simultaneous requests from one key may reasonably start refusing.
    _PROBE_CONCURRENCY = 8

    # A model that has not answered by now is not one to offer: the catalogue
    # is on the path of a page load, and a model too slow to say "hi" in this
    # long is too slow to hold a conversation. Several NIMs never answer at
    # all — openai/gpt-oss-20b hangs for 45s, two of the guard models for
    # longer — and excluding them is the point rather than a side effect.
    _PROBE_TIMEOUT = 20

    # Safety classifiers and guardrails are not general chat or embedding
    # models for this application, so keep them out of the catalogue entirely.
    _SAFETY_MODEL_MARKERS = tuple(marker.value for marker in NvidiaSafetyModelMarker)

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url=base_url, source=NVIDIA)

    def _host_url(self, source: str) -> str:
        return self.settings.NVIDIA_API_BASE_URL

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.NVIDIA_API_KEY}"}

    async def list_models(self) -> list[dict]:
        """Every model NVIDIA publishes, ids qualified with the vendor."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers)
                response.raise_for_status()
                payload = response.json()

        except Exception as exc:
            raise LLMProviderError(
                f"Could not list NVIDIA models at {self.base_url}: {exc} " "(is NVIDIA_API_KEY valid?)"
            ) from exc

        models = []

        for entry in payload.get("data", []):
            tag = entry.get("id")
            if not tag:
                continue

            models.append(
                {
                    "id": qualify(self.source, tag),
                    "tag": tag,
                    "source": self.source,
                    # Hosted: there is no local footprint to report, and the
                    # API publishes neither family nor parameter count.
                    "size_gb": None,
                    "family": (tag.split("/")[0] if "/" in tag else None),
                    "parameters": self._parameters_of(tag),
                    "capabilities": None,
                }
            )

        models.sort(key=lambda m: m["id"])

        models = [model for model in models if not self._is_safety_model(model["tag"])]

        return await self._only_usable(models)

    @classmethod
    def _is_safety_model(cls, tag: str) -> bool:
        """Whether an NVIDIA model is a safety classifier or guard model."""
        normalized = tag.lower().replace("/", "-")
        return any(marker in normalized for marker in cls._SAFETY_MODEL_MARKERS)

    async def _only_usable(self, models: list[dict]) -> list[dict]:
        """*models*, minus what this account cannot call, each one classified.

        Two endpoints, because a model serves one or the other: an embedding
        NIM answers /embeddings and refuses /chat/completions, so testing
        everything for chat would drop every embedding model from the
        catalogue — including the one this application is configured to use.

        A name hint picks which endpoint to try first; the endpoint, not the
        name, gives the answer. A "…embed…" model that does not embed falls
        through to the chat probe rather than being discarded on its name.
        """
        hinted = [m for m in models if self._looks_like_embedding(m)]
        rest = [m for m in models if not self._looks_like_embedding(m)]

        # The width probe is the access test as well — and it populates
        # _embedding_cache, so the later _probe_all pass costs nothing.
        widths = await asyncio.gather(*(self.embedding_dimensions(m["tag"]) for m in hinted))

        embedders = []

        for model, width in zip(hinted, widths):
            if width:
                # Evidence, not a guess: it embedded. Recording it here is
                # what keeps an embedding model out of the chat list.
                model["capabilities"] = [EMBEDDING]
                embedders.append(model)
            else:
                rest.append(model)

        chatters = await self._only_callable(rest)

        usable = sorted(embedders + chatters, key=lambda m: m["id"])

        self.logger.info(
            "NVIDIA: %d of %d models usable with this key (%d embedding)",
            len(usable),
            len(models),
            len(embedders),
        )

        return usable

    def _looks_like_embedding(self, model: dict) -> bool:
        return any(hint in model["tag"].lower() for hint in self._EMBEDDING_HINTS)

    async def _only_callable(self, models: list[dict]) -> list[dict]:
        """*models*, minus the ones this account is not entitled to call.

        The catalogue and the entitlement are different things: /v1/models
        returns everything NVIDIA publishes — 82 of them — while a given key
        may call a handful, and the rest answer

            404 Function '<uuid>': Not found for account '<account>'

        only once a real request is made. Listing them all is what made the
        picker offer models that answer 404 the moment they are chosen.

        Two passes, because entitlement and usability are different questions
        and only the first one is free.

        **Entitlement** costs nothing. An empty `messages` list is invalid for
        every model, and NVIDIA checks access *before* it validates the body:
        a reachable model answers 400 (your body is wrong), an unavailable one
        404 (the model is not yours). Neither runs inference.

        **Usability** needs a real request, because passing entitlement says
        nothing about whether a model accepts the request this application
        sends. Measured across the models that pass the first pass: some
        reject the output-cap field outright ("extra_forbidden"), some are not
        chat models at all and answer 500, and some never answer. All of them
        used to sit in the picker looking selectable and fail on first use.

        So the survivors are asked to generate one token, with the same field
        names NvidiaChatProvider sends — taken from the provider class itself
        rather than repeated here, so the probe cannot test a shape the
        provider no longer uses.
        """
        semaphore = asyncio.Semaphore(self._PROBE_CONCURRENCY)

        async with httpx.AsyncClient(timeout=self._PROBE_TIMEOUT) as client:

            async def ask(tag: str, body: dict) -> int | None:
                """The status, or None when it never answered."""
                try:
                    async with semaphore:
                        response = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=self._headers,
                            json={"model": tag, **body},
                        )
                    return response.status_code

                except Exception as exc:
                    self.logger.debug("Probe failed for %r: %s", tag, exc)
                    return None

            async def usable(model: dict) -> bool:
                cached = self._access_cache.get(model["id"])
                if cached is not None:
                    return cached

                # Free: is it ours at all?
                entitled = await ask(model["tag"], {"messages": []})

                if entitled is None:
                    # Never answered. Not offered now, but not written down as
                    # a no either: a large model can miss the deadline waking
                    # up and answer comfortably once warm, and a cached no
                    # would hide it until the process restarts.
                    return False

                if entitled == 404:
                    ok = False
                else:
                    # One token, shaped exactly like a real request.
                    from factories.llmchatting import NvidiaChatProvider

                    status = await ask(
                        model["tag"],
                        {
                            "messages": [{"role": "user", "content": "hi"}],
                            "temperature": 0,
                            NvidiaChatProvider._MAX_TOKENS_FIELD: 1,
                        },
                    )

                    if status is None:
                        return False  # same reasoning: no verdict recorded

                    ok = status == 200

                self._access_cache[model["id"]] = ok

                return ok

            verdicts = await asyncio.gather(*(usable(m) for m in models))

        return [model for model, ok in zip(models, verdicts) if ok]

    @classmethod
    def _parameters_of(cls, tag: str) -> str | None:
        """The parameter count a tag advertises, in Ollama's spelling.

        Returned as "11B" rather than a number so the picker can treat every
        source's value the same way — Ollama reports "8.0B" from the tag list,
        and a model that names no size (minimax-m3, nemotron-parse) reports
        nothing here rather than a guess.
        """
        match = cls._PARAMETERS.search(tag.lower())

        return f"{match.group(1)}B".upper() if match else None

    async def _probe_all(self, models: list[dict]) -> list[dict]:
        """Widths for the plausible embedding models, in order.

        Concurrent, unlike the Ollama path: these are remote calls against a
        hosted service, so there is no model being swapped in and out of a
        GPU and nothing to be gained by queueing them.
        """
        # Already settled by _only_usable, and already in _embedding_cache —
        # this pass just reads the widths back out in the caller's order.
        candidates = [model for model in models if _can(model, EMBEDDING)]

        widths = dict(
            zip(
                (m["tag"] for m in candidates),
                await asyncio.gather(*(self.embedding_dimensions(m["tag"]) for m in candidates)),
            )
        )

        return [widths.get(model["tag"]) for model in models]

    async def embedding_dimensions(self, tag: str) -> int | None:
        """Vector width for *tag*, or None if it cannot embed (or is not ours).

        A 404 here means the account cannot call the model, which for the
        picker's purposes is the same answer as "cannot embed": it must not be
        offered as an embedding model, because choosing it would rebuild a
        chat's index against a model that never responds.
        """
        model_id = qualify(self.source, tag)

        if model_id in self._embedding_cache:
            return self._embedding_cache[model_id]

        dimensions: int | None = None

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers,
                    json={
                        "model": tag,
                        "input": ["probe"],
                        # Required by the asymmetric models; harmless to the
                        # rest. Same value NvidiaEmbeddingProvider sends for a
                        # stored document.
                        "input_type": "passage",
                    },
                )

                if response.status_code == 200:
                    vectors = response.json().get("data") or []
                    if vectors:
                        dimensions = len(vectors[0].get("embedding") or []) or None

        except Exception as exc:
            self.logger.debug("Embedding probe failed for %r: %s", model_id, exc)

        self._embedding_cache[model_id] = dimensions

        return dimensions


# source -> the controller that speaks it. Adding a vendor is an entry here
# plus its class above; nothing else in the application branches on a source.
_CONTROLLERS = {
    LOCAL: ModelController,
    CLOUD: ModelController,
    NVIDIA: NvidiaModelController,
}


def for_source(source: str) -> ModelController:
    """The controller for *source*, defaulting to a local Ollama host.

    Callers hold a qualified id and want to ask its own host a question —
    "can this embed?" — without knowing which kind of host that is.
    """
    controller = _CONTROLLERS.get(source, ModelController)

    # Only the Ollama controller distinguishes hosts; the vendor ones are
    # their own source and take no argument.
    return controller(source=source) if controller is ModelController else controller()
