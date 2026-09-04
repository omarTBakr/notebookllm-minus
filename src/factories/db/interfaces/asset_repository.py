from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import Asset


class AssetRepository(ABC):
    @abstractmethod
    async def create_asset(self, asset: Asset) -> str:
        pass

    @abstractmethod
    async def update_asset(self, asset: Asset) -> str:
        pass

    @abstractmethod
    async def get_asset(self, asset_id: str) -> Asset:
        pass

    @abstractmethod
    async def iter_project_assets(self, project_id: str) -> AsyncIterator[Asset]:
        """Every asset in one project, oldest first, unpaginated."""

    @abstractmethod
    async def iter_assets_for_projects(self, project_ids: list[str]) -> AsyncIterator[Asset]:
        pass

    @abstractmethod
    async def find_by_content_hash(self, project_id: str, content_hash: str) -> Asset | None:
        """The project's asset with these exact bytes, or None.

        Must not load file_bytes: this runs on every upload, and the point is
        to answer before anything large is read or written.
        """
        pass

    @abstractmethod
    async def rename(self, asset_id: str, name: str) -> None:
        pass

    @abstractmethod
    async def delete_asset(self, asset_id: str) -> bool:
        """Remove one asset. True if a row went, False if there was none."""
        pass

    @abstractmethod
    async def delete_assets_for_project(self, project_id: str) -> None:
        pass
