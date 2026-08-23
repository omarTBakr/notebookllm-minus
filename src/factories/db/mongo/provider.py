"""MongoDB + Qdrant implementation of DbProvider.

Document repositories are backed by MongoDB via Motor.
The vector repository is backed by Qdrant (embedded or server).
"""

from motor.motor_asyncio import AsyncIOMotorClient

from enums import DistanceMethod
from exceptions import DbConnectionError
from utils import get_logger
from utils.config import Settings

from ..interfaces.asset_repository import AssetRepository
from ..interfaces.chat_repository import ChatRepository
from ..interfaces.chunk_repository import ChunkRepository
from ..interfaces.message_repository import MessageRepository
from ..interfaces.project_repository import ProjectRepository
from ..interfaces.provider import DbProvider
from ..interfaces.session_repository import SessionRepository
from ..interfaces.user_repository import UserRepository
from ..interfaces.vector_repository import VectorRepository

from .asset_repository import MongoAssetRepository
from .chat_repository import MongoChatRepository
from .chunk_repository import MongoChunkRepository
from .message_repository import MongoMessageRepository
from .project_repository import MongoProjectRepository
from .session_repository import MongoSessionRepository
from .user_repository import MongoUserRepository
from .vector_repository import QdrantVectorRepository


class MongoProvider(DbProvider):
    """DbProvider: documents in MongoDB, vectors in Qdrant."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self.logger = get_logger(type(self).__module__)

        self._vector_repo = QdrantVectorRepository(
            path=getattr(settings, "VECTOR_DB_PATH", None),
            url=getattr(settings, "VECTOR_DB_URL", None),
            api_key=getattr(settings, "VECTOR_DB_API_KEY", None),
            distance_method=DistanceMethod(settings.VECTOR_DB_DISTANCE_METHOD),
        )

    # --- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        if self.client is not None:
            return
        try:
            self.client = AsyncIOMotorClient(self.settings.MONGO_URI)
            self.db = self.client[self.settings.MONGO_DB_NAME]
            await self.client.admin.command("ping")
            self.logger.info("Connected to MongoDB (database=%s)", self.settings.MONGO_DB_NAME)
        except Exception as exc:
            self.client = None
            self.db = None
            raise DbConnectionError(f"MongoDB connection failed: {exc}") from exc

        await self._vector_repo.connect()

    async def disconnect(self) -> None:
        await self._vector_repo.disconnect()
        if self.client:
            self.client.close()
            self.client = None
            self.db = None
            self.logger.info("Closed MongoDB connection")

    async def setup_indexes(self) -> None:
        if self.db is None:
            raise DbConnectionError("Cannot setup indexes; not connected")

        await MongoProjectRepository(self.db).create_index([("project_id", 1)], unique=True)

        chunk_repo = MongoChunkRepository(self.db)
        await chunk_repo.create_index([("project_id", 1), ("created_at", -1)])
        await chunk_repo.create_index([("project_id", 1), ("asset_id", 1)])
        await chunk_repo.create_index([("project_id", 1), ("asset_id", 1), ("chunk_order", 1)])

        await MongoAssetRepository(self.db).create_index([("project_id", 1), ("created_at", -1)])
        await MongoUserRepository(self.db).create_index([("user_id", 1)], unique=True)

        session_repo = MongoSessionRepository(self.db)
        await session_repo.create_index([("session_id", 1)], unique=True)
        await session_repo.create_index([("user_id", 1), ("created_at", -1)])

        chat_repo = MongoChatRepository(self.db)
        await chat_repo.create_index([("chat_id", 1)], unique=True)
        await chat_repo.create_index([("session_id", 1), ("created_at", -1)])

        await MongoMessageRepository(self.db).create_index([("chat_id", 1), ("created_at", 1)])

        self.logger.info("MongoDB indexes ensured")

    # --- document repositories -----------------------------------------------

    def users(self) -> UserRepository:
        return MongoUserRepository(self.db)

    def sessions(self) -> SessionRepository:
        return MongoSessionRepository(self.db)

    def chats(self) -> ChatRepository:
        return MongoChatRepository(self.db)

    def messages(self) -> MessageRepository:
        return MongoMessageRepository(self.db)

    def projects(self) -> ProjectRepository:
        return MongoProjectRepository(self.db)

    def assets(self) -> AssetRepository:
        return MongoAssetRepository(self.db)

    def chunks(self) -> ChunkRepository:
        return MongoChunkRepository(self.db)

    # --- vector repository ---------------------------------------------------

    def vectors(self) -> VectorRepository:
        return self._vector_repo
