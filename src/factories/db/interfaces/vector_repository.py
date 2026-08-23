"""The contract every vector store implements.

Deliberately narrow: create a collection, put vectors in, get the nearest ones
back. Anything a specific engine offers beyond that stays behind its own
implementation, so a second backend never has to fake a feature it lacks.
"""

from abc import ABC, abstractmethod



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
