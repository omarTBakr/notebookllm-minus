from enum import Enum

from qdrant_client import models  # ty: ignore[unresolved-import]


class VectorDBProvider(str, Enum):
    """Vector stores this application knows how to build."""

    QDRANT = "qdrant"


class DistanceMethod(str, Enum):
    """Similarity metric a collection is created with.

    Fixed at creation time — changing it later means rebuilding the collection.
    """

    COSINE = "cosine"
    DOT    = "dot"
    EUCLID = "euclid"


class DistanceFunction(str, Enum):
    """Qdrant distance functions."""

    COSINE  = models.Distance.COSINE
    DOT     = models.Distance.DOT
    EUCLID  = models.Distance.EUCLID