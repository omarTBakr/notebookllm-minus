from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import Message

class MessageRepository(ABC):
    @abstractmethod
    async def create_message(self, message: Message) -> str:
        pass

    @abstractmethod
    async def iter_chat_messages(self, chat_id: str) -> AsyncIterator[Message]:
        pass

    @abstractmethod
    async def get_recent_history(self, chat_id: str, limit: int) -> list[dict]:
        pass
