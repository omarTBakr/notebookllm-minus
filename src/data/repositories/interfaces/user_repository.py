from abc import ABC, abstractmethod
from typing import AsyncIterator

from data.models import User


class UserRepository(ABC):
    @abstractmethod
    async def create_user(self, user: User) -> str:
        pass

    @abstractmethod
    async def get_user(self, user_id: str) -> User:
        pass

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """The user with this (already lowercased) email, or None."""
        pass

    @abstractmethod
    async def set_credentials(self, user_id: str, email: str, password_hash: str) -> None:
        """Give a user their login. Raises DuplicateEmailError if the email is taken."""
        pass

    @abstractmethod
    async def set_password_hash(self, user_id: str, password_hash: str) -> None:
        pass

    @abstractmethod
    async def rename(self, user_id: str, label: str) -> None:
        pass

    @abstractmethod
    async def delete_user(self, user_id: str) -> bool:
        """Remove one user row. True if a row went, False if there was none.

        Only the user: their sessions, chats and everything under them are the
        caller's to clear first. Nothing in either store cascades on its own.
        """
        pass

    @abstractmethod
    async def count_users(self) -> int:
        pass

    @abstractmethod
    async def iter_users(self) -> AsyncIterator[User]:
        pass
