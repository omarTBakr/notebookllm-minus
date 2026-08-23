from abc import ABC, abstractmethod

from .asset_repository import AssetRepository
from .chat_repository import ChatRepository
from .chunk_repository import ChunkRepository
from .message_repository import MessageRepository
from .project_repository import ProjectRepository
from .session_repository import SessionRepository
from .user_repository import UserRepository
from .vector_repository import VectorRepository


class DbProvider(ABC):
    """Single provider for all persistence: document store + vector store.

    A Mongo deployment pairs with a separate vector DB (Qdrant); a Postgres
    deployment handles both sides itself. Either way, callers ask ``app.db``
    for a repository and never touch the engine directly.
    """

    # --- lifecycle -----------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Open all connections. Called once, from the app's lifespan."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close all connections. Safe to call when never connected."""

    @abstractmethod
    async def setup_indexes(self) -> None:
        """Ensure indexes / schemas exist. Idempotent."""

    # --- document repositories -----------------------------------------------

    @abstractmethod
    def users(self) -> UserRepository:
        pass

    @abstractmethod
    def sessions(self) -> SessionRepository:
        pass

    @abstractmethod
    def chats(self) -> ChatRepository:
        pass

    @abstractmethod
    def messages(self) -> MessageRepository:
        pass

    @abstractmethod
    def projects(self) -> ProjectRepository:
        pass

    @abstractmethod
    def assets(self) -> AssetRepository:
        pass

    @abstractmethod
    def chunks(self) -> ChunkRepository:
        pass

    # --- vector repository ---------------------------------------------------

    @abstractmethod
    def vectors(self) -> VectorRepository:
        pass
