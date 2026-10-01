from abc import ABC, abstractmethod

from .artifact_repository import ArtifactRepository
from .asset_repository import AssetRepository
from .chat_repository import ChatRepository
from .chunk_repository import ChunkRepository
from .ingest_batch_repository import IngestBatchRepository
from .message_repository import MessageRepository
from .personal_user_info_repository import PersonalUserInfoRepository
from .project_repository import ProjectRepository
from .session_repository import SessionRepository
from .task_repository import TaskRepository
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
    def artifacts(self) -> ArtifactRepository:
        """Study material generated from this notebook's documents."""

    @abstractmethod
    def chunks(self) -> ChunkRepository:
        pass

    @abstractmethod
    def ingest_batches(self) -> IngestBatchRepository:
        """Parsed pages held between the stages of one ingestion."""

    @abstractmethod
    def tasks(self) -> TaskRepository:
        pass

    # Not abstract, deliberately: `/memory` is a Postgres-only feature for now
    # (see plan `i-want-to-add-linear-pebble`), and forcing every backend to
    # implement it would mean either a stub Mongo repository nobody uses or a
    # MongoProvider that cannot be instantiated. Raising here rather than in
    # each backend keeps that one decision in one place.
    def personal_user_info(self) -> PersonalUserInfoRepository:
        raise NotImplementedError("personal_user_info() is only implemented on the Postgres backend")

    # --- vector repository ---------------------------------------------------

    @abstractmethod
    def vectors(self) -> VectorRepository:
        pass
