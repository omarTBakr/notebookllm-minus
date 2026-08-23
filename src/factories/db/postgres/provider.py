"""PostgreSQL implementation of DbProvider.

Handles both document storage and vector storage (via pgvector).
"""

import asyncpg
from typing import Optional

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


class PostgresProvider(DbProvider):
    """DbProvider: both documents and vectors in PostgreSQL (+ pgvector)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(type(self).__module__)
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self.pool is not None:
            return
        
        try:
            self.pool = await asyncpg.create_pool(
                self.settings.vector_db_url,
                min_size=1,
                max_size=20,
            )
            self.logger.info("Connected to PostgreSQL (database=%s)", self.settings.POSTGRES_DB)
        except Exception as exc:
            self.pool = None
            raise DbConnectionError(f"PostgreSQL connection failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
            self.logger.info("Closed PostgreSQL connection pool")

    async def setup_indexes(self) -> None:
        if self.pool is None:
            raise DbConnectionError("Cannot setup indexes; not connected to PostgreSQL")

        try:
            async with self.pool.acquire() as conn:
                # 1. Enable pgvector
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

                # 2. Create tables if they don't exist
                
                # users table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id VARCHAR(24) PRIMARY KEY,
                        user_id VARCHAR(200) UNIQUE NOT NULL,
                        label VARCHAR(200),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                ''')

                # sessions table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS sessions (
                        id VARCHAR(24) PRIMARY KEY,
                        session_id VARCHAR(200) UNIQUE NOT NULL,
                        user_id VARCHAR(200) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id, created_at DESC);
                ''')

                # chats table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS chats (
                        id VARCHAR(24) PRIMARY KEY,
                        chat_id VARCHAR(200) UNIQUE NOT NULL,
                        session_id VARCHAR(200) NOT NULL,
                        user_id VARCHAR(200) NOT NULL,
                        title VARCHAR(200) NOT NULL,
                        lang VARCHAR(8) NOT NULL DEFAULT 'en',
                        generation_model VARCHAR(200),
                        embedding_model VARCHAR(200),
                        embedding_dimensions INT,
                        temperature DOUBLE PRECISION,
                        max_tokens INT,
                        chunk_size INT,
                        overlap_size INT,
                        web_search BOOLEAN NOT NULL DEFAULT FALSE,
                        excluded_assets JSONB,
                        has_documents BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_chats_session_id ON chats (session_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_chats_user_id ON chats (user_id, created_at DESC);
                ''')

                # messages table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id VARCHAR(24) PRIMARY KEY,
                        message_id VARCHAR(200) UNIQUE NOT NULL,
                        chat_id VARCHAR(200) NOT NULL,
                        role VARCHAR(50) NOT NULL,
                        content TEXT NOT NULL,
                        citations JSONB,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages (chat_id, created_at ASC);
                ''')

                # projects table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS projects (
                        id VARCHAR(24) PRIMARY KEY,
                        project_id VARCHAR(200) UNIQUE NOT NULL,
                        name VARCHAR(200) NOT NULL,
                        description TEXT,
                        chunks_ids JSONB,
                        assets_ids JSONB,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                ''')

                # assets table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS assets (
                        id VARCHAR(24) PRIMARY KEY,
                        asset_id VARCHAR(200) UNIQUE NOT NULL,
                        project_id VARCHAR(200) NOT NULL,
                        asset_type VARCHAR(50) NOT NULL,
                        name VARCHAR(200) NOT NULL,
                        description TEXT,
                        file_bytes BYTEA,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_assets_project_id ON assets (project_id, created_at DESC);
                ''')

                # chunks table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS chunks (
                        id VARCHAR(24) PRIMARY KEY,
                        project_id VARCHAR(24) NOT NULL,
                        asset_id VARCHAR(200),
                        chunk_order INT NOT NULL,
                        chunk_content TEXT NOT NULL,
                        chunk_metadata JSONB,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_chunks_project_id ON chunks (project_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_chunks_project_asset ON chunks (project_id, asset_id);
                    CREATE INDEX IF NOT EXISTS idx_chunks_project_asset_order ON chunks (project_id, asset_id, chunk_order);
                ''')
                
                # We do not create a single vectors table here. 
                # Qdrant dynamically creates collections. We'll do the same in VectorRepository
                # (creating a table per collection) or we can have one massive `vectors` table
                # with a `collection_name` column. Given pgvector, a table per collection
                # is generally better so we can tune the vector index per collection/dimension.

            self.logger.info("PostgreSQL tables and indexes ensured")
        except Exception as exc:
            raise DbConnectionError(f"Failed to setup PostgreSQL indexes: {exc}") from exc

    def users(self) -> UserRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresUserRepository(self.pool)

    def sessions(self) -> SessionRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresSessionRepository(self.pool)

    def chats(self) -> ChatRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresChatRepository(self.pool)

    def messages(self) -> MessageRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresMessageRepository(self.pool)

    def projects(self) -> ProjectRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresProjectRepository(self.pool)

    def assets(self) -> AssetRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresAssetRepository(self.pool)

    def chunks(self) -> ChunkRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresChunkRepository(self.pool)

    def vectors(self) -> VectorRepository:
        if self.pool is None:
            raise DbConnectionError("Not connected")
        return PostgresVectorRepository(
            pool=self.pool,
            distance_method=self.settings.VECTOR_DB_DISTANCE_METHOD,
        )
