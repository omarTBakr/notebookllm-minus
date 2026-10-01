import uuid
from collections.abc import Callable

from data.models import (
    Chat,
    ChatModel,
    ChunkModel,
    ProjectModel,
    Session,
    SessionModel,
    UserModel,
)
from shared.exceptions import InvalidInputError
from shared.utils import split_source

from ..core import BaseService
from ..llm import for_source


class ConversationService(BaseService):
    """Sessions and chats: create, list, rename, and file one under the other."""

    def __init__(self, db, nlp_for_chat: Callable | None = None):
        super().__init__()
        self.db = db
        self.nlp_for_chat = nlp_for_chat
        self.users = UserModel(db)
        self.sessions = SessionModel(db)
        self.chats = ChatModel(db)

    # --- sessions ---------------------------------------------------------

    async def default_session(self, user_id: str) -> str:
        """The session a notebook is filed under.

        Notebooks are the UI's unit of work; sessions are a layer the interface
        no longer shows. Rather than change the schema, every profile keeps one
        implicit session and notebooks hang off it — so Chat.session_id stays a
        real foreign key and nothing had to be migrated.
        """
        async for session in self.sessions.iter_user_sessions(user_id):
            return session.session_id

        session = Session(session_id=str(uuid.uuid4()), user_id=user_id, title="Default")
        await self.sessions.create_session(session)

        return session.session_id

    async def create_session(self, user_id: str, title: str) -> Session:
        await self.users.get_user(user_id)

        session = Session(session_id=str(uuid.uuid4()), user_id=user_id, title=title)
        await self.sessions.create_session(session)
        return session

    async def list_sessions(self, user_id: str) -> list[Session]:
        await self.users.get_user(user_id)
        return [s async for s in self.sessions.iter_user_sessions(user_id)]

    # --- chats ------------------------------------------------------------

    async def create_chat(self, session_id: str, title: str, lang: str) -> Chat:
        session = await self.sessions.get_session(session_id)
        return await self._create_chat(session_id, session.user_id, title, lang)

    async def create_user_chat(self, user_id: str, title: str, lang: str) -> Chat:
        """A notebook under a profile, without the caller knowing about sessions."""
        await self.users.get_user(user_id)
        session_id = await self.default_session(user_id)
        return await self._create_chat(session_id, user_id, title, lang)

    async def _create_chat(self, session_id: str, user_id: str, title: str, lang: str) -> Chat:
        chat = Chat(
            chat_id=str(uuid.uuid4()),
            session_id=session_id,
            user_id=user_id,
            title=title,
            lang=lang,
        )
        await self.chats.create_chat(chat)
        return chat

    async def get_chat(self, chat_id: str) -> Chat:
        return await self.chats.get_chat(chat_id)

    async def list_chats(self, session_id: str) -> list[Chat]:
        await self.sessions.get_session(session_id)
        return [c async for c in self.chats.iter_session_chats(session_id)]

    async def list_user_chats(self, user_id: str) -> list[Chat]:
        """Every notebook a profile owns, newest first — a flat list, not a
        session tree, since the UI has no session concept."""
        await self.users.get_user(user_id)
        return [c async for c in self.chats.iter_user_chats(user_id)]

    async def rename_chat(self, chat_id: str, title: str) -> str:
        await self.chats.get_chat(chat_id)
        title = title.strip()
        await self.chats.rename(chat_id, title)
        return title

    async def set_models(
        self,
        chat_id: str,
        generation_model: str | None,
        embedding_model: str | None,
    ) -> tuple[Chat, int]:
        """Point one chat at different models. Returns the chat and how many chunks were re-embedded.

        Switching the embedding model **rebuilds this chat's index**: the vector
        width is fixed when a collection is created, so old vectors are unusable
        by the new model. The chunks are already stored, so the rebuild
        re-embeds them rather than asking for the documents again.

        Needs `nlp_for_chat`, for the rebuild.
        """
        chat = await self.chats.get_chat(chat_id)

        dimensions = None

        if embedding_model:
            # Probe on the source the id names, not always the local one — a
            # cloud or NVIDIA embedding model would otherwise be rejected as
            # incapable, since the local Ollama has never heard of it.
            source, tag = split_source(embedding_model)
            dimensions = await for_source(source).embedding_dimensions(tag)

            if not dimensions:
                raise InvalidInputError(
                    f"{embedding_model!r} cannot produce embeddings — "
                    "pick one from the embedding list at GET /chat/models"
                )

        await self.chats.set_models(
            chat_id,
            generation_model=generation_model,
            embedding_model=embedding_model,
            embedding_dimensions=dimensions,
        )

        reindexed = 0

        if embedding_model and chat.has_documents:
            project = await ProjectModel(self.db).get_project(chat_id)

            nlp = self.nlp_for_chat(await self.chats.get_chat(chat_id))

            # reset=True drops the old collection so the new one is created at
            # the new width. Without it every insert would be rejected for a
            # dimension mismatch — the failure mode EMBEDDING_MODEL_SIZE exists
            # to prevent.
            result = await nlp.index_chunks(
                chunk_model=ChunkModel(self.db),
                project_object_id=project.id,
                project_id=chat_id,
                reset=True,
            )

            # Explicitly, because index_chunks no longer does it: everywhere
            # else the ingestion chain's last link builds the index, and this is
            # the one path with no chain. reset=True above dropped the
            # collection and its index with it, so skipping this would leave the
            # new model's vectors searchable only by exact scan — slow, but not
            # broken, and therefore silent.
            await nlp.build_index(chat_id)

            reindexed = result["chunks_indexed"]

            self.logger.info(
                "Re-indexed chat %r under %r: %d chunk(s)",
                chat_id,
                embedding_model,
                reindexed,
            )

        return await self.chats.get_chat(chat_id), reindexed

    async def set_settings(self, chat_id: str, changes: dict) -> Chat:
        """Write only the fields sent, so each control saves on its own."""
        await self.chats.get_chat(chat_id)
        await self.chats.set_settings(chat_id, changes)
        return await self.chats.get_chat(chat_id)
