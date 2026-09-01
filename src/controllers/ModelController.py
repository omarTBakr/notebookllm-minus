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

import httpx  # ty: ignore[unresolved-import]

from enums import LLMChattingProvider
from exceptions import LLMProviderError
from utils import CLOUD, LOCAL, NVIDIA, host_for, qualify

from .BaseController import BaseController


def _source_of(backend: str) -> str:
    """Which source a .env backend name corresponds to.

    The inverse of utils.backend_for: "ollama" is the local host (a bare tag
    in .env has never meant the cloud one), and any other backend names itself.
    """
    backend = str(getattr(backend, "value", backend)).strip().lower()

    return LOCAL if backend == LLMChattingProvider.OLLAMA.value else backend


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

        return models

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
        """Drop the cache, so newly pulled models are picked up."""
        cls._embedding_cache.clear()

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
        lives. Every model is offered for chat, since Ollama will generate
        with any of them and NVIDIA's catalogue is chat-first. Only those that
        answer an embed probe are offered for embedding, each carrying the
        vector width its collections must be built at.
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
                if width
            ]
            embedding.sort(key=lambda m: m["id"])

        return {
            "chat": models,
            "embedding": embedding,
            "current": {
                # A .env default names a bare tag, and which source that tag
                # belongs to is whatever backend .env selected — an ollama
                # backend means the local host, a vendor backend means the
                # vendor. Qualifying everything as local, as this once did,
                # left the picker showing "missing" for the model actually in
                # use the moment EMBEDDING_BACKEND stopped being ollama.
                "chat": qualify(
                    _source_of(self.settings.GENERATION_BACKEND),
                    self.settings.GENERATION_MODEL_ID,
                ),
                "embedding": qualify(
                    _source_of(self.settings.EMBEDDING_BACKEND),
                    self.settings.EMBEDDING_MODEL_ID,
                ),
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

    # Substrings that make a model worth an embed probe. A heuristic on the
    # *candidate set* only: the answer still comes from the endpoint.
    _EMBEDDING_HINTS = ("embed", "retriev")

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
                    "parameters": None,
                    "capabilities": None,
                }
            )

        models.sort(key=lambda m: m["id"])

        return models

    async def _probe_all(self, models: list[dict]) -> list[dict]:
        """Widths for the plausible embedding models, in order.

        Concurrent, unlike the Ollama path: these are remote calls against a
        hosted service, so there is no model being swapped in and out of a
        GPU and nothing to be gained by queueing them.
        """
        candidates = [
            model
            for model in models
            if any(hint in model["tag"].lower() for hint in self._EMBEDDING_HINTS)
        ]

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
