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

What is left here is the part that needs a host, a client or a cache. The pure
functions moved out: `utils.model_capabilities` reads what a listing entry or a
tag says about a model, `utils.provider_errors` reads what a vendor's refusal
means. Both are answers about a *string*, not about this service's state,
and keeping them here meant a caller had to build a service — and know which
of the two classes carried the one it wanted — to ask.
"""

import asyncio
import time

import httpx  # ty: ignore[unresolved-import]

from shared.exceptions import LLMProviderError
from shared.utils import (
    ANTHROPIC,
    CLOUD,
    COMPLETION,
    EMBEDDING,
    GOOGLE,
    LOCAL,
    NVIDIA,
    OPENROUTER,
    can,
    default_chat_model,
    default_embedding_model,
    host_for,
    qualify,
    rate_limited,
    reached_generation,
    unavailable_reason,
)

from ..core.BaseService import BaseService


class ModelService(BaseService):
    """Lists installed Ollama models and probes their capabilities."""

    # qualified model id -> dimensions, or None when it cannot embed.
    #
    # Keyed by the *qualified* id: the same tag on two hosts is two models,
    # and one of them answering an embed probe says nothing about the other.
    #
    # Deliberately a *class* attribute. Probing means asking the model to embed
    # something, which costs a real inference call — around five seconds for a
    # 8B embedding model. The routes build a fresh ModelService per request,
    # so an instance-level cache was thrown away every time and the catalogue
    # re-probed all seven models on every call: eighteen seconds, during which
    # the settings dropdowns sit empty. Sharing it across instances makes the
    # first call slow and every later one instant.
    _embedding_cache: dict[str, int | None] = {}

    # qualified model id -> why it cannot be used, or None when it answered.
    #
    # A class attribute for the same reason as _embedding_cache: the routes
    # build a fresh service per request, so an instance cache would re-probe
    # every vendor on every catalogue call.
    _configured_cache: dict[str, str | None] = {}

    # Deliberately too small to answer with. The probe asks "will this model
    # take my request", not "what does it say", and on a thinking model those
    # have very different prices: Gemini 3.x reasons before it writes, so a
    # budget big enough for real text costs 30 seconds, while one too small
    # comes back in about a second having proved everything that matters —
    # the key authenticated, the model exists, the account may call it, and
    # generation started. Truncation *is* the pass; see utils.reached_generation.
    _CONFIGURED_PROBE_MAX_TOKENS = 16

    _CONFIGURED_PROBE_TIMEOUT = 10

    # How long to leave a model alone after a probe that settled nothing.
    _PROBE_COOLDOWN = 300

    # Model ids whose probe has not come back yet, so two catalogue calls in
    # the same second do not both pay for one.
    _probes_in_flight: dict[str, "asyncio.Task"] = {}

    # Model id -> monotonic time before which not to probe again.
    _probe_cooldown: dict[str, float] = {}

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

        Takes the bare tag — the host is this service's own. The only
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

    def _hosts(self, sources: list[str] | None = None) -> list["ModelService"]:
        """Every source we are configured to talk to.

        A source that is not configured is left out rather than listed and
        broken: no OLLAMA_CLOUD_BASE_URL means no cloud models in the picker,
        no NVIDIA_API_KEY means no NVIDIA ones, and likewise OPENROUTER_API_KEY.
        """
        hosts: list[ModelService] = [ModelService(source=LOCAL)]

        if self.settings.OLLAMA_CLOUD_BASE_URL:
            hosts.append(ModelService(source=CLOUD))

        # Imported here, not at module scope: the registry lives in the package
        # __init__ because it needs both this class and its vendor subclasses,
        # so importing it at the top would be a cycle.
        from . import for_source

        if self.settings.NVIDIA_API_KEY:
            hosts.append(for_source(NVIDIA))

        if self.settings.OPENROUTER_API_KEY:
            hosts.append(for_source(OPENROUTER))

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
        from application.providers.provider_cache import ProviderCache

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
            if rate_limited(exc):
                # Also no verdict, and the most important one not to record.
                # A 429 says the account is busy, not that the model is
                # unusable — and since the probe itself consumes quota,
                # caching this would let the check condemn the model on
                # evidence it manufactured.
                self._probe_cooldown[model_id] = time.monotonic() + self._PROBE_COOLDOWN
                self.logger.debug("Probe for %r rate-limited; no verdict", model_id)
                return

            if not reached_generation(exc):
                reason = unavailable_reason(exc)
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
                if width and can(model, EMBEDDING)
            ]
            embedding.sort(key=lambda m: m["id"])

        return {
            "chat": [m for m in models if can(m, COMPLETION)],
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
