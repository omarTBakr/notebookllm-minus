from abc import ABC, abstractmethod
from datetime import datetime

from data.models import AuthSession


class AuthSessionRepository(ABC):
    """Signed-in browsers, looked up by the sha256 of their cookie token."""

    @abstractmethod
    async def create(self, session: AuthSession) -> None:
        pass

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        pass

    @abstractmethod
    async def touch(self, token_hash: str, last_seen_at: datetime, expires_at: datetime) -> None:
        """Record activity and slide the expiry forward."""
        pass

    @abstractmethod
    async def delete(self, token_hash: str) -> bool:
        pass

    @abstractmethod
    async def delete_for_user(self, user_id: str, keep_token_hash: str | None = None) -> int:
        """End every session of a user, optionally sparing the current one."""
        pass

    @abstractmethod
    async def delete_expired(self, now: datetime) -> int:
        pass
