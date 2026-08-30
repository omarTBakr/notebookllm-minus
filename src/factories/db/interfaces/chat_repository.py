from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import Chat

class ChatRepository(ABC):
    @abstractmethod
    async def create_chat(self, chat: Chat) -> str:
        pass

    @abstractmethod
    async def get_chat(self, chat_id: str) -> Chat:
        pass

    @abstractmethod
    async def rename(self, chat_id: str, title: str) -> None:
        pass

    @abstractmethod
    async def set_has_documents(self, chat_id: str, has_documents: bool) -> None:
        pass

    @abstractmethod
    async def set_models(
        self,
        chat_id: str,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        pass

    @abstractmethod
    async def set_settings(self, chat_id: str, changes: dict) -> None:
        pass

    @abstractmethod
    async def delete_chat(self, chat_id: str) -> bool:
        """Remove one chat row. True if a row went, False if there was none.

        The chat only. Its messages, project, assets, chunks and vectors are
        the caller's to clear — a chat_id *is* a project_id, so what hangs off
        it reaches further than this repository can see.
        """
        pass

    @abstractmethod
    async def iter_user_chats(self, user_id: str) -> AsyncIterator[Chat]:
        pass

    @abstractmethod
    async def iter_session_chats(self, session_id: str) -> AsyncIterator[Chat]:
        pass
