"""NVIDIA's hosted catalogue, in the same shape as an Ollama host's.

Split out of ModelService because it is a third of that file and answers a
narrower question: everything here exists because NVIDIA's catalogue and a
key's entitlements are different things, which is not true of the local host
the base class was written for.
"""

import asyncio

import httpx  # ty: ignore[unresolved-import]

from shared.exceptions import LLMProviderError
from shared.utils import (
    EMBEDDING,
    NVIDIA,
    can,
    is_safety_model,
    looks_like_embedding,
    parameters_of,
    qualify,
)

from .ModelService import ModelService


class NvidiaModelService(ModelService):
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

    # qualified id -> True when this account may call the model. Beside
    # _embedding_cache, on the class and for the same reason: the routes build
    # a service per request, and this is the answer to a network round trip.
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
                    "parameters": parameters_of(tag),
                    "capabilities": None,
                }
            )

        models.sort(key=lambda m: m["id"])

        models = [model for model in models if not is_safety_model(model["tag"])]

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
        hinted = [m for m in models if looks_like_embedding(m["tag"])]
        rest = [m for m in models if not looks_like_embedding(m["tag"])]

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
                    from application.providers.chatting import NvidiaChatProvider

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

    async def _probe_all(self, models: list[dict]) -> list[dict]:
        """Widths for the plausible embedding models, in order.

        Concurrent, unlike the Ollama path: these are remote calls against a
        hosted service, so there is no model being swapped in and out of a
        GPU and nothing to be gained by queueing them.
        """
        # Already settled by _only_usable, and already in _embedding_cache —
        # this pass just reads the widths back out in the caller's order.
        candidates = [model for model in models if can(model, EMBEDDING)]

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
