"""Builds the one multimodal embedder from IMAGE_EMBEDDING_* settings."""

from __future__ import annotations

from .ImageEmbeddingInterface import ImageEmbeddingInterface
from .LlamaCppImageEmbeddingProvider import LlamaCppImageEmbeddingProvider
from .OllamaImageEmbeddingProvider import OllamaImageEmbeddingProvider


class ImageEmbeddingFactory:
    def __init__(self, settings) -> None:
        self.settings = settings

    def create(self) -> ImageEmbeddingInterface:
        s = self.settings
        common = {
            "model_id": s.IMAGE_EMBEDDING_MODEL_ID,
            "embedding_size": s.IMAGE_EMBEDDING_SIZE,
            "timeout_s": s.IMAGE_EMBEDDING_TIMEOUT_S,
        }
        backend = (s.IMAGE_EMBEDDING_BACKEND or "ollama").lower()
        if backend == "ollama":
            return OllamaImageEmbeddingProvider(base_url=s.ollama_base_url, **common)
        if backend == "llamacpp":
            return LlamaCppImageEmbeddingProvider(base_url=s.IMAGE_EMBEDDING_URL, **common)
        raise ValueError(f"Unknown IMAGE_EMBEDDING_BACKEND {s.IMAGE_EMBEDDING_BACKEND!r} (ollama | llamacpp)")
