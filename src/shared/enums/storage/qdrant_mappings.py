"""Fixed lookup table translating DistanceMethod into Qdrant's own vocabulary.

Same reasoning as pgvector_mappings.py: the backend speaks its own wire format
for a concept this project already has an enum for. This one used to live as
a private `DistanceFunction` enum on QdrantVectorRepository; it lives beside
the enum it keys off instead.
"""

from qdrant_client import models  # ty: ignore[unresolved-import]

from .backends import DistanceMethod

DISTANCE_METHOD_TO_QDRANT: dict[DistanceMethod, models.Distance] = {
    DistanceMethod.COSINE: models.Distance.COSINE,
    DistanceMethod.DOT: models.Distance.DOT,
    DistanceMethod.EUCLID: models.Distance.EUCLID,
}
