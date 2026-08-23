from bson.objectid import ObjectId  # ty: ignore[unresolved-import]
from abc import ABC, abstractmethod

from models.db_schema import Project

class ProjectRepository(ABC):
    @abstractmethod
    async def create_project(self, project: Project) -> str:
        pass

    @abstractmethod
    async def update_project(self, project: Project) -> ObjectId:
        pass

    @abstractmethod
    async def get_project(self, project_id: str) -> Project:
        pass

    @abstractmethod
    async def list_projects(self) -> list[Project]:
        pass

    @abstractmethod
    async def rename(self, project_id: str, name: str) -> None:
        pass

    @abstractmethod
    async def delete_project(self, project_id: str) -> None:
        pass

    @abstractmethod
    async def add_asset_id(self, project_id: str, asset_object_id: str) -> None:
        pass

    @abstractmethod
    async def add_chunk_ids(self, project_id: str, chunk_object_ids: list[str]) -> None:
        pass
