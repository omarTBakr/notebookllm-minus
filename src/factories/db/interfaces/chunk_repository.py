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
    async def iter_project_chunks(
        self, project_id: str, asset_id: str | None = None
    ) -> AsyncIterator[DataChunk]:
        pass

    @abstractmethod
    async def delete_chunks_for_project(self, project_id: str) -> None:
        pass

    @abstractmethod
    async def delete_chunks_for_asset(self, project_id: str, asset_id: str) -> None:
        pass

    @abstractmethod
    async def count_project_chunks(self, project_id: str, asset_id: str | None = None) -> int:
        pass

    @abstractmethod
    async def has_asset_chunks(self, project_id: str, asset_id: str) -> bool:
        pass
