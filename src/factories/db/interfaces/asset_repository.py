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
    async def iter_assets_for_projects(self, project_ids: list[str]) -> AsyncIterator[Asset]:
        pass

    @abstractmethod
    async def rename(self, asset_id: str, name: str) -> None:
        pass

    @abstractmethod
    async def delete_assets_for_project(self, project_id: str) -> None:
        pass
