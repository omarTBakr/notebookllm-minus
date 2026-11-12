"""Multimodal embeddings from a llama.cpp ``llama-server``.

Started with the model's GGUF plus its ``--mmproj`` vision projector and
``--embedding``. A picture goes in as a multimodal prompt: the text holds
llama.cpp's media marker and the picture rides beside it, base64-encoded,
which is how llama-server's ``/embeddings`` accepts images. Text goes in as a
plain string, through the same model, so both land in one space.
"""

from __future__ import annotations

import base64

import httpx

from shared.enums import EmbeddingInputType
from shared.exceptions import EmbeddingError

from .ImageEmbeddingInterface import ImageEmbeddingInterface

MEDIA_MARKER = "<__media__>"


class LlamaCppImageEmbeddingProvider(ImageEmbeddingInterface):
    def __init__(self, model_id: str, embedding_size: int, base_url: str, timeout_s: float = 60.0) -> None:
        super().__init__(model_id=model_id, embedding_size=embedding_size, timeout_s=timeout_s)
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_s)

    async def _post(self, content: list) -> list[list[float]]:
        try:
            response = await self.client.post("/embeddings", json={"content": content})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"llama.cpp image embedding failed at {self.base_url}: {exc}") from exc

        body = response.json()
        # Either a list of {"index", "embedding"} or {"data": [...]}, by version.
        items = body.get("data", body) if isinstance(body, dict) else body
        items = sorted(items, key=lambda item: item.get("index", 0))
        vectors = []
        for item in items:
            vector = item["embedding"]
            # Unpooled responses nest one vector per token; pooled ones do not.
            if vector and isinstance(vector[0], list):
                vector = vector[-1]
            vectors.append(vector)
        return vectors

    async def _embed_images(self, images: list[bytes]) -> list[list[float]]:
        return await self._post(
            [
                {"prompt_string": MEDIA_MARKER, "multimodal_data": [base64.b64encode(image).decode("ascii")]}
                for image in images
            ]
        )

    async def _embed_texts(self, texts: list[str], input_type: EmbeddingInputType) -> list[list[float]]:
        return await self._post(list(texts))
