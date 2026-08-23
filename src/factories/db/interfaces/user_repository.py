from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import User

class UserRepository(ABC):
    @abstractmethod
    async def create_user(self, user: User) -> str:
        pass

    @abstractmethod
    async def get_user(self, user_id: str) -> User:
        pass

    @abstractmethod
    async def rename(self, user_id: str, label: str) -> None:
        pass

    @abstractmethod
    async def count_users(self) -> int:
        pass

    @abstractmethod
    async def iter_users(self) -> AsyncIterator[User]:
        pass
