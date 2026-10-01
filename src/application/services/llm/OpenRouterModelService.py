"""OpenRouter's catalogue, in the same shape as an Ollama host's.

The opposite problem to NVIDIA's. There, the catalogue says nothing about
capability or entitlement and every model had to be asked; here ``GET /models``
describes each one — what it reads and writes, what parameters it accepts, what
it costs — and any key may call any listed model. So there is no probing for
chat, and what the listing says is what the picker shows.
"""

import asyncio

import httpx  # ty: ignore[unresolved-import]

from shared.exceptions import LLMProviderError
from shared.utils import (
    COMPLETION,
    EMBEDDING,
    OPENROUTER,
    is_safety_model,
    parameters_of,
    qualify,
)

from .ModelService import ModelService


class OpenRouterModelService(ModelService):
    """OpenRouter's hosted catalogue.

    Chat models come from ``/models``, filtered to those that produce text.
    Embedding models are a separate listing (``/embeddings/models``) — the chat
    one leaves them out — and only those are asked for their vector width,
    because the width is what a collection built with the model needs.
    """

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url=base_url, source=OPENROUTER)

    def _host_url(self, source: str) -> str:
        return self.settings.OPENROUTER_API_BASE_URL

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.OPENROUTER_API_KEY}"}

    @staticmethod
    def _capabilities(entry: dict) -> list[str]:
        """What the listing says a model can do, in Ollama's vocabulary.

        Every entry here already passed the text-output filter, so completion
        is a given; the rest are read off the architecture and the parameters
        the model accepts.
        """
        architecture = entry.get("architecture") or {}
        parameters = entry.get("supported_parameters") or []

        capabilities = [COMPLETION]

        if "image" in (architecture.get("input_modalities") or []):
            capabilities.append("vision")
        if "tools" in parameters:
            capabilities.append("tools")
        if "reasoning" in parameters or "include_reasoning" in parameters:
            capabilities.append("thinking")

        return capabilities

    def _entry(self, entry: dict, capabilities: list[str]) -> dict | None:
        tag = entry.get("id")
        if not tag:
            return None

        return {
            "id": qualify(self.source, tag),
            "tag": tag,
            "source": self.source,
            # Hosted: no local footprint to report.
            "size_gb": None,
            "family": tag.split("/")[0] if "/" in tag else None,
            "parameters": parameters_of(tag),
            "capabilities": capabilities,
            "name": entry.get("name"),
            "context_length": entry.get("context_length"),
        }

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}{path}", headers=self._headers)
            response.raise_for_status()
            return response.json()

    async def list_models(self) -> list[dict]:
        """Every text-producing model OpenRouter lists, plus its embedding models."""
        try:
            payload = await self._get("/models")

        except Exception as exc:
            raise LLMProviderError(
                f"Could not list OpenRouter models at {self.base_url}: {exc} (is OPENROUTER_API_KEY valid?)"
            ) from exc

        models = []

        for entry in payload.get("data", []):
            outputs = (entry.get("architecture") or {}).get("output_modalities") or ["text"]

            # An image-generation model answers a chat request with a picture
            # this application has nowhere to put.
            if "text" not in outputs:
                continue

            model = self._entry(entry, self._capabilities(entry))

            if model and not is_safety_model(model["tag"]):
                models.append(model)

        models.extend(await self._list_embedding_models())

        models.sort(key=lambda m: m["id"])

        self.logger.info("OpenRouter: %d models listed", len(models))

        return models

    async def _list_embedding_models(self) -> list[dict]:
        """OpenRouter's embedding models, or nothing if the listing is unavailable.

        Not fatal: an account with no embedding listing still has its chat
        models, and the picker then simply offers no OpenRouter embedder.
        """
        try:
            payload = await self._get("/embeddings/models")

        except Exception as exc:
            self.logger.debug("No OpenRouter embedding listing: %s", exc)
            return []

        embedders = [self._entry(entry, [EMBEDDING]) for entry in payload.get("data", [])]

        return [model for model in embedders if model]

    async def _probe_all(self, models: list[dict]) -> list[int | None]:
        """Widths for the embedding models, in the caller's order.

        Concurrent: remote calls to a hosted service, nothing being swapped in
        and out of a GPU.
        """
        candidates = [m for m in models if EMBEDDING in (m.get("capabilities") or [])]

        widths = dict(
            zip(
                (m["tag"] for m in candidates),
                await asyncio.gather(*(self.embedding_dimensions(m["tag"]) for m in candidates)),
            )
        )

        return [widths.get(model["tag"]) for model in models]

    async def embedding_dimensions(self, tag: str) -> int | None:
        """Vector width for *tag*, or None if it cannot embed."""
        model_id = qualify(self.source, tag)

        if model_id in self._embedding_cache:
            return self._embedding_cache[model_id]

        dimensions: int | None = None

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers,
                    json={"model": tag, "input": ["probe"]},
                )

                if response.status_code == 200:
                    vectors = response.json().get("data") or []
                    if vectors:
                        dimensions = len(vectors[0].get("embedding") or []) or None

        except Exception as exc:
            self.logger.debug("Embedding probe failed for %r: %s", model_id, exc)

        self._embedding_cache[model_id] = dimensions

        return dimensions
