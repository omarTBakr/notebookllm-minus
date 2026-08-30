"""PostgreSQL implementation of DbProvider.

Handles both document storage and vector storage (via pgvector).
"""

from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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

from .asset_repository import PostgresAssetRepository
from .chat_repository import PostgresChatRepository
from .chunk_repository import PostgresChunkRepository
from .message_repository import PostgresMessageRepository
from .project_repository import PostgresProjectRepository
from .session_repository import PostgresSessionRepository
from .user_repository import PostgresUserRepository
from .vector_repository import PostgresVectorRepository

ALEMBIC_INI = Path(__file__).parent / "alembic.ini"

# Any int64 will do; it only has to be the same one in every process. "nblm".
_MIGRATION_LOCK_KEY = 0x6E626C6D


class PostgresProvider(DbProvider):
    """DbProvider: both documents and vectors in PostgreSQL (+ pgvector)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(type(self).__module__)
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    async def connect(self) -> None:
        if self.engine is not None:
            return

        engine = create_async_engine(
            self.settings.postgres_async_url,
            pool_size=5,
            max_overflow=15,
            # Connections that died while idle (a container restart, a network
            # blip) are otherwise handed out and fail on the caller's query.
            pool_pre_ping=True,
        )

        try:
            # create_async_engine is lazy, so without this a wrong DSN or a
            # stopped server surfaces on the first request instead of here.
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            await engine.dispose()
            raise DbConnectionError(f"PostgreSQL connection failed: {exc}") from exc

        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self.logger.info("Connected to PostgreSQL (database=%s)", self.settings.POSTGRES_DB)

    async def disconnect(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None
            self.logger.info("Closed PostgreSQL connection pool")

    async def setup_indexes(self) -> None:
        """Ensure schemas exist. Idempotent — Alembic owns the DDL.

        The tables are described in factories/db/postgres/alembic/versions/;
        this only brings the database up to the newest one. Running it here
        rather than as a deploy step keeps the app self-installing, which is
        what a local RAG project wants.
        """
        if self.engine is None:
            raise DbConnectionError("Cannot run migrations; not connected to PostgreSQL")

        cfg = Config(str(ALEMBIC_INI))

        try:
            async with self.engine.begin() as conn:
                # Several uvicorn workers boot at once and would all try to
                # migrate. A transaction-scoped advisory lock makes the rest
                # wait; by the time they get it, they are already at head and
                # the upgrade is a no-op. Released when this block commits.
                await conn.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": _MIGRATION_LOCK_KEY},
                )
                await conn.run_sync(self._run_upgrade, cfg)

            self.logger.info("PostgreSQL schema is at the latest migration")
        except Exception as exc:
            raise DbConnectionError(f"Failed to migrate PostgreSQL schema: {exc}") from exc

    @staticmethod
    def _run_upgrade(connection: Connection, cfg: Config) -> None:
        """Run `alembic upgrade head` over a connection we already hold.

        env.py picks this up and skips building an engine of its own — which
        would block on the advisory lock this connection is holding.
        """
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")

    # --- repositories --------------------------------------------------------

    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is None:
            raise DbConnectionError("Not connected")
        return self.session_factory

    def users(self) -> UserRepository:
        return PostgresUserRepository(self._sessions())

    def sessions(self) -> SessionRepository:
        return PostgresSessionRepository(self._sessions())

    def chats(self) -> ChatRepository:
        return PostgresChatRepository(self._sessions())

    def messages(self) -> MessageRepository:
        return PostgresMessageRepository(self._sessions())

    def projects(self) -> ProjectRepository:
        return PostgresProjectRepository(self._sessions())

    def assets(self) -> AssetRepository:
        return PostgresAssetRepository(self._sessions())

    def chunks(self) -> ChunkRepository:
        return PostgresChunkRepository(self._sessions())

    def vectors(self) -> VectorRepository:
        return PostgresVectorRepository(
            session_factory=self._sessions(),
            distance_method=self.settings.VECTOR_DB_DISTANCE_METHOD,
            index_type=self.settings.VECTOR_DB_INDEX_TYPE,
        )
