from collections.abc import Callable
from pathlib import Path

from data.models import (
    Asset,
    AssetModel,
    Chat,
    ChatModel,
    ChunkModel,
    Project,
    ProjectModel,
    TaskModel,
    UserModel,
)
from shared.constants import DEFAULT_CHAT_TITLE
from shared.enums import AssetType
from shared.exceptions import AssetNotFoundError

from ..core import BaseService
from ..ingest import AssetIngestService, QueuedIngestion


class SourceService(BaseService):
    """The documents attached to a notebook: listing, choosing, renaming,
    removing, reading back, and attaching new ones.

    chat_id *is* project_id, so every lookup here is keyed on the chat.
    `nlp_for_chat` is only needed by `delete`, to name the vector collection.
    """

    def __init__(self, db, nlp_for_chat: Callable | None = None):
        super().__init__()
        self.db = db
        self.nlp_for_chat = nlp_for_chat
        self.chats = ChatModel(db)
        self.assets = AssetModel(db)
        self.chunks = ChunkModel(db)

    async def get_chat(self, chat_id: str) -> Chat:
        return await self.chats.get_chat(chat_id)

    async def list_sources(self, chat_id: str) -> tuple[Chat, list[Asset]]:
        chat = await self.chats.get_chat(chat_id)
        return chat, [a async for a in self.assets.iter_assets_for_projects([chat_id])]

    async def select(self, chat_id: str, excluded_assets: list[str]) -> None:
        """Choose which sources this notebook answers from."""
        await self.chats.get_chat(chat_id)
        await self.chats.set_settings(chat_id, {"excluded_assets": excluded_assets})

    async def rename(self, chat_id: str, asset_id: str, name: str) -> None:
        """Only the asset document changes. The name is copied into chunk
        metadata and vector payloads at index time, but nothing reads those
        copies for display — citations resolve the name through ``asset_id`` —
        so there is nothing to cascade."""
        await self.chats.get_chat(chat_id)
        await self.assets.rename(asset_id, name)

    async def owned(self, chat_id: str, asset_id: str) -> tuple[Chat, Asset]:
        """The asset, if it is in this notebook.

        One that belongs to another notebook is reported the same way a missing
        one is, so the reply never confirms it exists somewhere else. Until
        this check existed, naming any valid chat alongside any asset id
        returned that asset's bytes.
        """
        chat = await self.chats.get_chat(chat_id)
        asset = await self.assets.get_asset(asset_id)

        if asset.project_id != chat_id:
            raise AssetNotFoundError(f"Asset {asset_id!r} is not in this notebook")

        return chat, asset

    async def locate(self, chat_id: str, asset_id: str, chunk_order: int):
        """The asset and one of its chunks."""
        _, asset = await self.owned(chat_id, asset_id)

        chunks = await self.chunks.get_chunks_by_orders(asset_id, [chunk_order])
        chunk = chunks.get(chunk_order)

        if chunk is None:
            raise AssetNotFoundError(f"Chunk {chunk_order} of asset {asset_id!r} was not found")

        return asset, chunk

    async def delete(self, chat_id: str, asset_id: str) -> dict:
        """Remove a source and everything derived from it.

        Three stores hold pieces of one document — the asset row, its chunks,
        and its vectors — and a source that is gone from the list while its
        vectors still answer questions is worse than one that was never
        deleted. They come out in derived-first order so a failure part-way
        through can be retried: vectors, then chunks, then the asset itself. The
        asset row is what the UI lists, so while it stands the delete is still
        visibly unfinished.
        """
        db = self.db
        chat, asset = await self.owned(chat_id, asset_id)

        project_object_id = await ProjectModel(db).update_project(
            Project(project_id=chat_id, name=chat.title, description=f"Chat {chat.title}")
        )

        # --- vectors ---
        collection = self.nlp_for_chat(chat).collection_name(chat_id)
        vectors_removed = 0

        if await db.vectors().collection_exists(collection):
            vectors_removed = await db.vectors().delete_by_metadata(
                collection_name=collection, key="asset_id", value=asset_id
            )

        # --- chunks ---
        removed_chunk_ids = await self.chunks.delete_chunks_for_asset(project_object_id, asset_id)

        # --- the asset itself ---
        await self.assets.delete_asset(asset_id)

        # A notebook with nothing left in it is not grounded any more, and the
        # composer reads this to decide whether an answer can cite anything.
        remaining = [a async for a in self.assets.iter_assets_for_projects([chat_id])]
        if not remaining:
            await self.chats.set_has_documents(chat_id, False)

        self.logger.info(
            "Deleted asset %r from chat %r: %s chunk(s), %s vector(s)",
            asset_id,
            chat_id,
            len(removed_chunk_ids),
            vectors_removed,
        )

        return {
            "name": asset.name,
            "chunks_deleted": len(removed_chunk_ids),
            "vectors_deleted": vectors_removed,
            "sources_remaining": len(remaining),
        }

    async def attach(
        self,
        chat: Chat,
        *,
        filename: str,
        content_type: str | None,
        file_bytes: bytes,
        chunk_size: int,
        overlap_size: int,
        asset_type: AssetType | None = None,
        content_hash: str | None = None,
        source_url: str = "",
    ) -> tuple[Asset, QueuedIngestion]:
        """Store one source and queue its ingestion.

        Deciding whether these bytes are already in the notebook, and queueing
        the chain that ingests them, is `AssetIngestService`.
        """
        chat_id = chat.chat_id
        ingest = AssetIngestService(self.db)

        asset = await ingest.store(
            chat_id,
            filename=filename,
            content_type=content_type,
            file_bytes=file_bytes,
            project_description=f"Chat {chat.title}",
            description=f"Attached to chat {chat_id}",
            asset_type=asset_type,
            content_hash=content_hash,
            source_url=source_url,
        )

        # Name an untitled notebook after the document that was just put in it.
        # Until now nothing named a notebook on upload, so it stayed "New chat"
        # until the first question renamed it to whatever was typed -- which
        # meant uploading an Arabic-named PDF and then asking in English
        # produced an English notebook with no relation to its contents.
        #
        # The document is the better name: it is what the notebook is *about*,
        # it arrives first, and it carries the user's own language. A question
        # still names a notebook that has no documents.
        if chat.title == DEFAULT_CHAT_TITLE:
            name = Path(filename).stem if asset.asset_type != AssetType.YOUTUBE else filename
            await self.chats.rename(chat_id, name[:200])

        # Everything above had to happen in the request: the bytes arrive on
        # this connection, identity is decided from them, and the asset_id is
        # minted here so the response can name it. Chunking and embedding do not
        # — they are the long part, and need no request state. The chain does
        # both, on the workers built for them.
        queued = await ingest.queue(
            chat_id,
            asset,
            {
                "project_id": chat_id,
                "asset_id": asset.asset_id,
                "chunk_size": chunk_size,
                "overlap_size": overlap_size,
                "reset": False,
            },
        )

        # Set now rather than when indexing finishes: the asset is stored and
        # the chat does have a document, and leaving it False until a worker
        # finishes would make the chat look empty while it ingests.
        await self.chats.set_has_documents(chat_id, True)

        return asset, queued

    async def indexing_task(self, chat_id: str, task_id: str | None):
        """The run to report on: the one named, else the newest unfinished one
        for the chat (what a page that reloaded mid-upload needs). None if none."""
        tasks = TaskModel(self.db)
        return await tasks.find_task(task_id) if task_id else await tasks.find_active_for_project(chat_id)

    async def list_for_user(self, user_id: str) -> tuple[dict[str, Chat], list[Asset]]:
        """Every document a user has uploaded, across all their chats.

        Because chat_id *is* project_id, a user's chat ids are exactly the
        project ids their assets are filed under — one lookup, then one query.
        """
        await UserModel(self.db).get_user(user_id)

        chats = {c.chat_id: c async for c in self.chats.iter_user_chats(user_id)}
        assets = [a async for a in self.assets.iter_assets_for_projects(list(chats))]
        return chats, assets
