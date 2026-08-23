from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import Session

class SessionRepository(ABC):
    @abstractmethod
    async def create_session(self, session: Session) -> str:
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> Session:
        pass

    @abstractmethod
    async def iter_user_sessions(self, user_id: str) -> AsyncIterator[Session]:
        pass
