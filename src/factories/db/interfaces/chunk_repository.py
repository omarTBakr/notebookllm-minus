from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import DataChunk


class ChunkRepository(ABC):
    @abstractmethod
    async def create_chunks(self, chunks: list[DataChunk]) -> list[str]:
        pass

    @abstractmethod
    async def iter_chunks(self, project_object_id: str) -> AsyncIterator[DataChunk]:
        pass

    @abstractmethod
    async def iter_project_chunks(self, project_id: str, asset_id: str | None = None) -> AsyncIterator[DataChunk]:
        pass

    @abstractmethod
    async def delete_chunks_for_project(self, project_id: str) -> None:
        pass

    @abstractmethod
    async def delete_chunks_for_asset(self, project_id: str, asset_id: str) -> list[str]:
        pass

    @abstractmethod
    async def count_project_chunks(self, project_id: str, asset_id: str | None = None) -> int:
        pass

    @abstractmethod
    async def has_asset_chunks(self, project_id: str, asset_id: str) -> bool:
        pass

    @abstractmethod
    async def set_chunk_summaries(self, summaries: dict[str, str]) -> int:
        """Write a one-line precis onto each chunk, keyed by chunk row id.

        Bulk rather than one call per chunk: the Studio generation task
        summarises a batch at a time, and a round trip per chunk over 694 of
        them would cost more than the model call that produced them.

        Returns how many rows were updated, so a caller can tell a no-op from a
        write without reading them back.
        """

    @abstractmethod
    async def count_unsummarised(self, project_id: str) -> int:
        """How many of a project's chunks have no summary yet.

        The denominator for progress, and the check that decides whether
        generation has any summarising left to do at all.
        """

    @abstractmethod
    async def get_chunks_by_orders(self, asset_id: str, chunk_orders: list[int]) -> dict[int, DataChunk]:
        """The named chunks of one asset, keyed by ``chunk_order``.

        For turning a search hit back into a place in the original document:
        the vector payload carries ``(asset_id, chunk_order)``, which
        ``NLPController._point_key`` already treats as a passage's stable
        identity, but the page number and the highlight geometry live on the
        chunk row.

        Keyed by ``chunk_order`` rather than returned as a list because the
        caller only ever looks values up, and a dict removes any question of
        whether the backend preserved the requested order or silently dropped
        an id it could not find.

        An empty ``chunk_orders`` returns ``{}`` without issuing a query.
        """
        pass
