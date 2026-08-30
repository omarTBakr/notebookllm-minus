from typing import AsyncIterator

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from exceptions import ChatNotFoundError, DbError
from models.db_schema import Chat
from .base_repository import PostgresBaseRepository, ChatRow
from ..interfaces.chat_repository import ChatRepository

# Fields a caller may hand to set_settings. Every one is a real column, and
# every one is a real Chat field — the assert below is what stops this list
# drifting from the model the way the old JSON blob silently did.
SETTABLE_FIELDS = frozenset(
    {
        "title",
        "lang",
        "generation_model",
        "embedding_model",
        "embedding_dimensions",
        "temperature",
        "max_tokens",
        "chunk_size",
        "overlap_size",
        "web_search",
        "highlight_color",
        "excluded_assets",
        "has_documents",
    }
)

assert SETTABLE_FIELDS <= set(Chat.model_fields), (
    f"set_settings would write fields Chat does not have: "
    f"{sorted(SETTABLE_FIELDS - set(Chat.model_fields))}"
)


class PostgresChatRepository(PostgresBaseRepository, ChatRepository):
    """PostgreSQL implementation of ChatRepository.

    Every Chat field is its own column. The previous version wrote a `name`
    and a `settings` JSON blob, neither of which existed on the table or the
    model, so no chat could be created or modified on this backend at all.
    """

    async def create_chat(self, chat: Chat) -> str:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    insert(ChatRow)
                    .values(
                        id=self._generate_id(),
                        chat_id=chat.chat_id,
                        session_id=chat.session_id,
                        # NOT NULL, and never supplied before this rewrite.
                        user_id=chat.user_id,
                        title=chat.title,
                        lang=chat.lang,
                        generation_model=chat.generation_model,
                        embedding_model=chat.embedding_model,
                        embedding_dimensions=chat.embedding_dimensions,
                        temperature=chat.temperature,
                        max_tokens=chat.max_tokens,
                        chunk_size=chat.chunk_size,
                        overlap_size=chat.overlap_size,
                        web_search=chat.web_search,
                        excluded_assets=chat.excluded_assets,
                        has_documents=chat.has_documents,
                        created_at=chat.created_at,
                        updated_at=chat.updated_at,
                    )
                    .on_conflict_do_nothing(index_elements=["chat_id"])
                )
            return chat.chat_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create chat: {exc}") from exc

    async def get_chat(self, chat_id: str) -> Chat:
        try:
            async with self.session_factory() as db:
                row = await db.scalar(select(ChatRow).where(ChatRow.chat_id == chat_id))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get chat: {exc}") from exc

        if row is None:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")

        return self._record_to_model(row, Chat)

    async def _patch(self, chat_id: str, changes: dict, what: str) -> None:
        """Apply *changes* to one chat, or raise if there is no such chat."""
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    update(ChatRow)
                    .where(ChatRow.chat_id == chat_id)
                    .values(updated_at=func.now(), **changes)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to {what} on chat: {exc}") from exc

        if result.rowcount == 0:
            raise ChatNotFoundError(f"Chat {chat_id!r} not found")

    async def rename(self, chat_id: str, title: str) -> None:
        await self._patch(chat_id, {"title": title}, "rename")

    async def set_has_documents(self, chat_id: str, has_documents: bool) -> None:
        await self._patch(chat_id, {"has_documents": has_documents}, "set_has_documents")

    async def set_models(
        self,
        chat_id: str,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        """Point a chat at specific models. Only the fields given are touched."""
        # None means "leave it alone", not "clear it".
        changes = {
            "generation_model": generation_model,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_dimensions,
        }
        changes = {k: v for k, v in changes.items() if v is not None}

        if not changes:
            return

        await self._patch(chat_id, changes, "set_models")

    async def set_settings(self, chat_id: str, changes: dict) -> None:
        """Apply the caller's settings dict, one column per key."""
        if not changes:
            return

        unknown = set(changes) - SETTABLE_FIELDS
        if unknown:
            raise DbError(f"Cannot set unknown chat settings: {sorted(unknown)}")

        await self._patch(chat_id, dict(changes), "set_settings")

    async def iter_user_chats(self, user_id: str) -> AsyncIterator[Chat]:
        """Every chat a user owns, across all their sessions."""
        try:
            async with self.session_factory() as db:
                # chats.user_id is populated and indexed, so this needs no join
                # through sessions — which is also how the Mongo backend reads
                # it, and the two should not answer differently.
                result = await db.stream_scalars(
                    select(ChatRow)
                    .where(ChatRow.user_id == user_id)
                    .order_by(ChatRow.created_at.desc())
                )
                async for row in result:
                    yield self._record_to_model(row, Chat)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate user chats: {exc}") from exc

    async def iter_session_chats(self, session_id: str) -> AsyncIterator[Chat]:
        try:
            async with self.session_factory() as db:
                result = await db.stream_scalars(
                    select(ChatRow)
                    .where(ChatRow.session_id == session_id)
                    .order_by(ChatRow.created_at.desc())
                )
                async for row in result:
                    yield self._record_to_model(row, Chat)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to iterate session chats: {exc}") from exc

    async def delete_chat(self, chat_id: str) -> bool:
        """Remove one chat row. The caller clears what hangs off it first."""
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(ChatRow).where(ChatRow.chat_id == chat_id)
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete chat: {exc}") from exc

        return result.rowcount > 0
