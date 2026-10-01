"""Fixed lookup tables translating this project's storage enums into pgvector's vocabulary.

The backend speaks its own wire format for a concept this project already has
an enum for (DistanceMethod). This table used to live as a private dict on
whichever class happened to consume it first; it lives beside the enum it keys
off instead.
"""

from .backends import DistanceMethod

# pgvector operator and index opclass per distance metric.
#   cosine: <=> / vector_cosine_ops
#   dot:    <#> / vector_ip_ops      (inner product; pgvector negates it)
#   euclid: <-> / vector_l2_ops
DISTANCE_METHOD_TO_PGVECTOR: dict[DistanceMethod, tuple[str, str]] = {
    DistanceMethod.COSINE: ("<=>", "vector_cosine_ops"),
    DistanceMethod.DOT: ("<#>", "vector_ip_ops"),
    DistanceMethod.EUCLID: ("<->", "vector_l2_ops"),
}
