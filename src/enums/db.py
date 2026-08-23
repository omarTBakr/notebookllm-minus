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