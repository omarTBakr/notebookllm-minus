"""Multimodal (picture + text) embedders for the image collections."""

from .ImageEmbeddingFactory import ImageEmbeddingFactory
from .ImageEmbeddingInterface import ImageEmbeddingInterface
from .LlamaCppImageEmbeddingProvider import LlamaCppImageEmbeddingProvider
from .OllamaImageEmbeddingProvider import OllamaImageEmbeddingProvider

__all__ = [
    "ImageEmbeddingFactory",
    "ImageEmbeddingInterface",
    "LlamaCppImageEmbeddingProvider",
    "OllamaImageEmbeddingProvider",
]
