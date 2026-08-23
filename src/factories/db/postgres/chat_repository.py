import json
from typing import AsyncIterator

from exceptions import ChatNotFoundError, DbError
from models.db_schema import Chat
from .base_repository import PostgresBaseRepository
from ..interfaces.chat_repository import ChatRepository


class PostgresChatRepository(PostgresBaseRepository, ChatRepository):
    """PostgreSQL implementation of ChatRepository."""

    async def create_chat(self, chat: Chat) -> str:
        record_id = self._generate_id()
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chats (id, chat_id, session_id, name, settings, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (chat_id) DO NOTHING
                    """,
                    record_id,
                    chat.chat_id,
                    chat.session_id,
                    chat.name,
                    json.dumps(chat.settings),
                    chat.created_at,
                    chat.updated_at,
                )
            return chat.chat_id
        except Exception as exc:
            raise DbError(f"Failed to create chat: {exc}") from exc

    async def get_chat(self, chat_id: str) -> Chat:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow(
                    "SELECT * FROM chats WHERE chat_id = $1", chat_id
                )
        except Exception as exc:
            raise DbError(f"Failed to get chat: {exc}") from exc

        if not record:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")
        
        return self._record_to_model(record, Chat)

    async def rename(self, chat_id: str, title: str) -> None:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE chats SET name = $1, updated_at = CURRENT_TIMESTAMP WHERE chat_id = $2",
                    title,
                    chat_id,
                )
                if result == "UPDATE 0":
                    raise ChatNotFoundError(f"Chat {chat_id!r} not found")
        except ChatNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to rename chat: {exc}") from exc

    async def set_has_documents(self, chat_id: str, has_documents: bool) -> None:
        try:
            async with self.pool.acquire() as conn:
                # We need to fetch the existing settings, modify it, and save it back
                # Or use JSONB functions in Postgres, but simple approach:
                # UPDATE chats SET settings = jsonb_set(settings, '{has_documents}', '"true"') ...
                
                # Using jsonb_set
                json_val = "true" if has_documents else "false"
                result = await conn.execute(
                    """
                    UPDATE chats 
                    SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{has_documents}', $1::jsonb), 
                        updated_at = CURRENT_TIMESTAMP 
                    WHERE chat_id = $2
                    """,
                    json_val,
                    chat_id,
                )
                if result == "UPDATE 0":
                    raise ChatNotFoundError(f"Chat {chat_id!r} not found")
        except ChatNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to set_has_documents on chat: {exc}") from exc

    async def set_models(
        self,
        chat_id: str,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow("SELECT settings FROM chats WHERE chat_id = $1", chat_id)
                if not record:
                    raise ChatNotFoundError(f"Chat {chat_id!r} not found")
                
                settings = json.loads(record['settings']) if record['settings'] else {}
                
                if generation_model is not None:
                    settings["generation_model"] = generation_model
                if embedding_model is not None:
                    settings["embedding_model"] = embedding_model
                if embedding_dimensions is not None:
                    settings["embedding_dimensions"] = embedding_dimensions
                
                await conn.execute(
                    "UPDATE chats SET settings = $1, updated_at = CURRENT_TIMESTAMP WHERE chat_id = $2",
                    json.dumps(settings),
                    chat_id
                )
        except ChatNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to set_models on chat: {exc}") from exc

    async def set_settings(self, chat_id: str, changes: dict) -> None:
        try:
            async with self.pool.acquire() as conn:
                record = await conn.fetchrow("SELECT settings FROM chats WHERE chat_id = $1", chat_id)
                if not record:
                    raise ChatNotFoundError(f"Chat {chat_id!r} not found")
                
                settings = json.loads(record['settings']) if record['settings'] else {}
                settings.update(changes)
                
                await conn.execute(
                    "UPDATE chats SET settings = $1, updated_at = CURRENT_TIMESTAMP WHERE chat_id = $2",
                    json.dumps(settings),
                    chat_id
                )
        except ChatNotFoundError:
            raise
        except Exception as exc:
            raise DbError(f"Failed to set_settings on chat: {exc}") from exc

    async def iter_user_chats(self, user_id: str) -> AsyncIterator[Chat]:
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # We join with sessions to filter by user_id
                    query = """
                        SELECT c.* FROM chats c
                        JOIN sessions s ON c.session_id = s.session_id
                        WHERE s.user_id = $1
                        ORDER BY c.created_at DESC
                    """
                    async for record in conn.cursor(query, user_id):
                        yield self._record_to_model(record, Chat)
        except Exception as exc:
            raise DbError(f"Failed to iterate user chats: {exc}") from exc

    async def iter_session_chats(self, session_id: str) -> AsyncIterator[Chat]:
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    query = "SELECT * FROM chats WHERE session_id = $1 ORDER BY created_at DESC"
                    async for record in conn.cursor(query, session_id):
                        yield self._record_to_model(record, Chat)
        except Exception as exc:
            raise DbError(f"Failed to iterate session chats: {exc}") from exc
