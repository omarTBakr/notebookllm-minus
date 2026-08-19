"""Discovers which models the local Ollama actually has, and what each can do.

Nothing here is hardcoded to a model name. Whether a model can embed is
determined by asking it to embed, because the tag list does not say — and the
answer also tells us the vector width, which the vector store needs.
"""

import httpx  # ty: ignore[unresolved-import]

from exceptions import LLMProviderError

from .BaseController import BaseController


class ModelController(BaseController):
    """Lists installed Ollama models and probes their capabilities."""

    def __init__(self, base_url: str | None = None) -> None:

        super().__init__()

        self.base_url = (base_url or self.settings.OLLAMA_BASE_URL).rstrip("/")

        # model_id -> dimensions, or None when it cannot embed. Probing costs a
        # real inference call, so the answer is remembered for the process.
        self._embedding_cache: dict[str, int | None] = {}

    async def list_models(self) -> list[dict]:
        """Every model the local Ollama has pulled."""

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
                    "id": entry["name"],
                    "size_gb": round(entry.get("size", 0) / 1e9, 2),
                    "family": details.get("family"),
                    "parameters": details.get("parameter_size"),
                }
            )

        models.sort(key=lambda m: m["id"])

        return models

    async def embedding_dimensions(self, model_id: str) -> int | None:
        """Vector width for *model_id*, or None if it cannot embed.

        The only reliable test is to try: Ollama answers "this model does not
        support embeddings" for generation-only models, and there is no flag on
        the tag list that predicts it.
        """
        if model_id in self._embedding_cache:
            return self._embedding_cache[model_id]

        dimensions: int | None = None

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": model_id, "input": ["probe"]},
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

    async def catalogue(self, probe_embeddings: bool = True) -> dict:
        """Installed models split into what they can be used for.

        Every model is offered for chat — Ollama will generate with any of
        them. Only those that answer an embed probe are offered for embedding,
        each carrying the vector width its collections must be built at.
        """
        models = await self.list_models()

        embedding = []

        if probe_embeddings:
            for model in models:
                dimensions = await self.embedding_dimensions(model["id"])
                if dimensions:
                    embedding.append({**model, "dimensions": dimensions})

        return {
            "chat": models,
            "embedding": embedding,
            "current": {
                "chat": self.settings.GENERATION_MODEL_ID,
                "embedding": self.settings.EMBEDDING_MODEL_ID,
                "embedding_dimensions": self.settings.EMBEDDING_MODEL_SIZE,
            },
        }
