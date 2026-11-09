"""Multimodal embeddings from the host's Ollama daemon (0.40 or newer).

EmbeddingGemma 2's "text-vision" builds ship a vision projector, and Ollama
embeds a picture through the same ``/api/embed`` call as text: an item of
``input`` may be an object ``{"image": "<base64>"}`` instead of a string.
Not documented at the time of writing; found by probing 0.40.15, where a
top-level ``images`` field is silently ignored (an empty result, no error)
and ``{"images": ...}`` inside ``input`` is rejected as an unknown field.

Measured on that build: a bar chart scored 0.77 against "a bar chart of
revenue by quarter" and 0.78 against its Arabic translation, versus 0.50 for
an unrelated sentence, so English and Arabic questions both find pictures.
"""

from __future__ import annotations

import base64

import httpx

from shared.enums import EmbeddingInputType
from shared.exceptions import EmbeddingError

from .ImageEmbeddingInterface import ImageEmbeddingInterface


class OllamaImageEmbeddingProvider(ImageEmbeddingInterface):
    def __init__(self, model_id: str, embedding_size: int, base_url: str, timeout_s: float = 60.0) -> None:
        super().__init__(model_id=model_id, embedding_size=embedding_size, timeout_s=timeout_s)
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s)

    async def _embed(self, body: dict) -> list[list[float]]:
        try:
            response = await self.client.post("/api/embed", json={"model": self.model_id, **body})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError(
                f"Ollama image embedding failed at {self.base_url}: {exc.response.status_code} "
                f"{exc.response.text[:300]} (is {self.model_id!r} pulled, on Ollama 0.40+?)"
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Ollama image embedding failed at {self.base_url}: {exc}") from exc
        return response.json().get("embeddings") or []

    async def _embed_images(self, images: list[bytes]) -> list[list[float]]:
        return await self._embed({"input": [{"image": base64.b64encode(image).decode("ascii")} for image in images]})

    async def _embed_texts(self, texts: list[str], input_type: EmbeddingInputType) -> list[list[float]]:
        return await self._embed({"input": list(texts)})
