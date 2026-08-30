from enum import Enum



class DbBackend(str, Enum):
    """Database backends this application knows how to build."""

    MONGO = "mongo"
    POSTGRES = "postgres"


class DistanceMethod(str, Enum):
    """Similarity metric a collection is created with.

    Fixed at creation time — changing it later means rebuilding the collection.
    """

    COSINE = "cosine"
    DOT    = "dot"
    EUCLID = "euclid"


class IndexType(str, Enum):
    """ANN index algorithm a vector collection's index is built with.

    Fixed at index-build time, not collection-creation time — changing it
    means dropping and rebuilding the index (not the collection) via
    VectorRepository.create_index(reset=True). IVFFLAT is pgvector-specific:
    Qdrant only builds HNSW and rejects it.
    """

    HNSW    = "hnsw"
    IVFFLAT = "ivfflat"