"""The contract every vector store implements.

Deliberately narrow: create a collection, put vectors in, get the nearest ones
back. Anything a specific engine offers beyond that stays behind its own
implementation, so a second backend never has to fake a feature it lacks.
"""

from abc import ABC, abstractmethod

from enums import IndexType


class VectorRepository(ABC):
    """Vector-store portion of a DbProvider."""

    # --- collections ---------------------------------------------------------

    @abstractmethod
    async def collection_exists(self, collection_name: str) -> bool: ...

    @abstractmethod
    async def list_collections(self) -> list[str]: ...

    @abstractmethod
    async def get_collection_info(self, collection_name: str) -> dict: ...

    @abstractmethod
    async def create_collection(
        self, collection_name: str, embedding_size: int, reset: bool = False
    ) -> bool:
        """Ensure a collection sized for *embedding_size* exists.

        Idempotent: returns ``False`` if it already existed and *reset* is
        false. With *reset*, the existing collection is dropped first — the
        only way to change vector size or distance metric, both of which are
        fixed at creation.
        """

    @abstractmethod
    async def delete_collection(self, collection_name: str) -> bool: ...

    @abstractmethod
    async def create_index(
        self,
        collection_name: str,
        embedding_size: int,
        index_type: IndexType | None = None,
        reset: bool = False,
    ) -> bool:
        """Build the ANN index for *collection_name*.

        Call once the collection holds its data, or is about to receive a
        bulk load via insert_many() — collections come back from
        create_collection() without an index, so an incremental insert never
        pays graph/cluster-maintenance cost per row, and IVFFlat's cluster
        count (which pgvector recommends picking from the row count) is only
        meaningful once there is data to cluster.

        *embedding_size* is only consulted by backends with a hard indexing
        width ceiling (pgvector's HNSW/IVFFlat top out at 2000 dimensions for
        the plain `vector` type); backends without one ignore it.

        *index_type* defaults to VECTOR_DB_INDEX_TYPE from Settings when
        omitted. Not every backend accepts every type — Qdrant only builds
        HNSW and raises UnsupportedProviderError for anything else.

        *reset* drops and rebuilds an existing index — the only way to change
        its type or parameters once built.

        Returns True once the index exists (built now, or already there), and
        False when the backend declined to build one without treating that as
        an error (e.g. Postgres past its dimension ceiling).
        """

    # --- records -------------------------------------------------------------

    @abstractmethod
    async def insert_many(
        self,
        collection_name: str,
        texts: list[str],
        vectors: list[list[float]],
        metadata: list[dict] | None = None,
        record_ids: list[str] | None = None,
        batch_size: int = 64,
    ) -> bool:
        """Upsert *texts* and *vectors* pairwise, in batches of *batch_size*."""

    @abstractmethod
    async def delete_by_metadata(
        self, collection_name: str, key: str, value: str
    ) -> int:
        """Delete every point whose ``metadata.<key>`` equals *value*.

        Returns the number of points removed, and 0 if the collection does not
        exist — deleting from nothing is not an error.
        """

    @abstractmethod
    async def search_by_vector(
        self,
        collection_name: str,
        vector: list[float],
        limit: int = 5,
        asset_ids: list[str] | None = None,
    ) -> list[dict]:
        """Nearest *limit* records, best first.

        *asset_ids* narrows the search to those sources; ``None`` searches all.
        Each result is ``{"id": ..., "score": float, "text": str, "metadata": dict}``.
        """
