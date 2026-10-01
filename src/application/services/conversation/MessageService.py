import uuid

from data.models import AssetModel, Chat, ChatModel, Message, MessageModel
from shared.constants import DEFAULT_CHAT_TITLE
from shared.enums import ChatRole

from ..core import BaseService
from ..rag.citations import keys_from_hits, resolve_pages


class MessageService(BaseService):
    """A chat's transcript: reading it back, and writing each turn into it."""

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.chats = ChatModel(db)
        self.messages = MessageModel(db)
        self.assets = AssetModel(db)

    async def source_names(self, chat_id: str) -> dict[str, str]:
        """``asset_id -> current name`` for every source in the chat.

        One pass answers two questions: which assets a search may touch, and
        what each is currently called.
        """
        return {asset.asset_id: asset.name async for asset in self.assets.iter_assets_for_projects([chat_id])}

    def page_lookup(self):
        """The callable `ChatService.answer_stream` uses to link a hit to a page."""
        return lambda hits: resolve_pages(self.db, keys_from_hits(hits))

    async def transcript(self, chat_id: str) -> list[dict]:
        """Every message, with its citations brought up to date."""
        await self.chats.get_chat(chat_id)

        # Citations were frozen with whatever the source was called when the
        # answer was written. Renaming a source has to reach old answers too, or
        # the transcript keeps citing a name that no longer exists anywhere.
        source_names = await self.source_names(chat_id)

        stored = [m async for m in self.messages.iter_chat_messages(chat_id)]

        # Answers written before citations carried a page have none stored. Fill
        # those in from the chunks they name, so an existing notebook gets
        # clickable citations without being re-indexed.
        #
        # Only where absent, and that asymmetry with the rename above is the
        # point: a rename is retroactively true of an old answer, but
        # re-processing a document remaps chunk_order onto different pages, so
        # back-filling a message that already has a page would silently move
        # the citation to a page that answer never read.
        missing = [
            (cite.get("asset_id"), cite.get("chunk_order"))
            for message in stored
            for cite in message.citations
            if cite.get("page_number") is None
            and cite.get("asset_id") is not None
            and cite.get("chunk_order") is not None
        ]
        pages = await resolve_pages(self.db, missing)

        def resolved(citations: list[dict]) -> list[dict]:
            out = []

            for cite in citations:
                fresh = {
                    **cite,
                    "source": source_names.get(cite.get("asset_id")) or cite.get("source"),
                }

                if fresh.get("page_number") is None:
                    located = pages.get((cite.get("asset_id"), cite.get("chunk_order")))
                    if located:
                        fresh.update(located)

                out.append(fresh)

            return out

        return [
            {
                "message_id": m.message_id,
                "role": m.role.value,
                "content": m.content,
                "citations": resolved(m.citations),
                "created_at": m.created_at.isoformat(),
            }
            for m in stored
        ]

    async def get_chat(self, chat_id: str) -> Chat:
        return await self.chats.get_chat(chat_id)

    async def recent_history(self, chat_id: str, limit: int):
        return await self.messages.get_recent_history(chat_id, limit)

    async def add_user_message(self, chat_id: str, text: str) -> None:
        await self.messages.create_message(
            Message(message_id=str(uuid.uuid4()), chat_id=chat_id, role=ChatRole.USER, content=text)
        )

    async def add_assistant_message(self, chat_id: str, text: str, citations: list[dict] | None = None) -> None:
        await self.messages.create_message(
            Message(
                message_id=str(uuid.uuid4()),
                chat_id=chat_id,
                role=ChatRole.ASSISTANT,
                content=text,
                citations=citations or [],
            )
        )

    async def name_chat_from_question(self, chat: Chat, history: list, text: str) -> None:
        """Name the chat after its first question, so the sidebar isn't a column
        of "New chat" -- but only if nothing has named it already.

        Uploading a document names the notebook after that document, and the
        document is the better name: it is what the notebook is about and it
        carries the user's own language. Renaming on "no history" alone
        overwrote it, so an Arabic PDF followed by an English question produced
        an English notebook.
        """
        if not history and chat.title == DEFAULT_CHAT_TITLE:
            title = text.strip()[:60]
            if title:
                await self.chats.rename(chat.chat_id, title)
