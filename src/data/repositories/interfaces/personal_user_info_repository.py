from abc import ABC, abstractmethod
from typing import AsyncIterator

from data.models import PersonalUserInfo


class PersonalUserInfoRepository(ABC):
    @abstractmethod
    async def upsert_fact(self, user_id: str, key: str, description: str) -> str:
        """Create or replace the fact `key` holds for `user_id`; returns its row id.

        Keyed on (user_id, key): a second call with the same pair overwrites
        `description` in place rather than adding a row, and returns the same
        row id every time — the id a caller then reuses as the vector store's
        record id, so re-saving a key updates its embedding instead of leaving
        an orphaned duplicate.
        """

    @abstractmethod
    async def list_facts(self, user_id: str) -> AsyncIterator[PersonalUserInfo]:
        pass

    @abstractmethod
    async def delete_fact(self, user_id: str, key: str) -> bool:
        pass
