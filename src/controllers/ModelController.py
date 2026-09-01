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

import httpx  # ty: ignore[unresolved-import]

from exceptions import LLMProviderError
from utils import (
    CLOUD,
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
COMPLETION = "completion"
EMBEDDING = "embedding"


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
                f"Could not list Ollama models at {self.base_url}: {exc} "
                "(is `ollama serve` running?)"
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
                    response = await client.post(
                        f"{self.base_url}/api/show", json={"model": model["tag"]}
                    )
                    if response.status_code == 200:
                        model["capabilities"] = response.json().get("capabilities")

                except Exception as exc:
                    # Still None afterwards, which reads as "unknown" — and an
                    # unknown model is offered for chat rather than hidden.
                    self.logger.debug(
                        "Could not read capabilities for %r: %s", model["id"], exc
                    )

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
            self.logger.warning(
                "Skipping %s Ollama at %s: %s", self.source, self.base_url, exc
            )
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

        for subclass in cls.__subclasses__():
            cache = getattr(subclass, "_access_cache", None)
            if cache is not None:
                cache.clear()

    def _hosts(self) -> list["ModelController"]:
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

        return hosts

    async def catalogue(self, probe_embeddings: bool = True) -> dict:
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
        hosts = self._hosts()

        listings = await asyncio.gather(*(host._safe_list() for host in hosts))

        models = [model for listing in listings for model in listing]
        models.sort(key=lambda m: m["id"])

        embedding = []

        if probe_embeddings:
            by_host = [
                [m for m in models if m["source"] == host.source] for host in hosts
            ]

            probed = await asyncio.gather(
                *(host._probe_all(mine) for host, mine in zip(hosts, by_host))
            )

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
                response = await client.get(
                    f"{self.base_url}/models", headers=self._headers
                )
                response.raise_for_status()
                payload = response.json()

        except Exception as exc:
            raise LLMProviderError(
                f"Could not list NVIDIA models at {self.base_url}: {exc} "
                "(is NVIDIA_API_KEY valid?)"
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

        return await self._only_usable(models)

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
        widths = await asyncio.gather(
            *(self.embedding_dimensions(m["tag"]) for m in hinted)
        )

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
                        return False      # same reasoning: no verdict recorded

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
                await asyncio.gather(
                    *(self.embedding_dimensions(m["tag"]) for m in candidates)
                ),
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
