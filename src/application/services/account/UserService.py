import uuid
from collections.abc import Callable

from data.models import (
    AssetModel,
    ChatModel,
    ChunkModel,
    MessageModel,
    ProjectModel,
    SessionModel,
    User,
    UserModel,
)
from shared.exceptions import ProjectNotFoundError

from ..core import BaseService
from ..rag import NLPService


class UserService(BaseService):
    """Profiles, and the cascade that removes one.

    `nlp_for_chat` builds the retrieval stack for a chat, because the name of a
    chat's vector collection depends on the embedding model that chat uses.
    """

    def __init__(self, db, nlp_for_chat: Callable[..., NLPService]):
        super().__init__()
        self.db = db
        self.nlp_for_chat = nlp_for_chat
        self.users = UserModel(db)

    async def create(self, label: str | None) -> User:
        """Mint a user. A blank label would make every entry in the picker look
        identical, so one is generated from the count."""
        label = (label or "").strip()

        if not label:
            label = f"User {await self.users.count_users() + 1}"

        user = User(user_id=str(uuid.uuid4()), label=label)
        await self.users.create_user(user)

        self.logger.debug("Created user %r (%s)", user.user_id, user.label)
        return user

    async def list_users(self) -> list[User]:
        return [u async for u in self.users.iter_users()]

    async def get(self, user_id: str) -> User:
        return await self.users.get_user(user_id)

    async def rename(self, user_id: str, label: str) -> str:
        await self.users.get_user(user_id)
        label = label.strip()
        await self.users.rename(user_id, label)
        return label

    async def delete(self, user_id: str) -> tuple[User, dict[str, int]]:
        """Remove a user and everything that belongs to them.

        Nothing in either store cascades on its own — there are no foreign keys
        on Postgres and no such concept on Mongo — so the whole tree is walked
        here:

            user -> sessions
                 -> chats -> messages
                          -> project -> assets
                                     -> chunks
                                     -> vector collection

        Derived-first throughout, and the owning row last at every level. A
        failure part-way leaves the user still listed with less under them,
        which is re-runnable; the reverse would leave orphans that nothing lists
        and nothing can reach to clean up.

        A chat_id *is* a project_id, which is what lets one loop clear a
        notebook's documents, chunks and vectors together.

        Returns the user and the counts removed, `sessions` included.
        """
        db = self.db

        # 404s if there is no such user, before anything is deleted.
        user = await self.users.get_user(user_id)

        chat_model = ChatModel(db)
        project_model = ProjectModel(db)
        removed = {"chats": 0, "messages": 0, "assets": 0, "chunks": 0, "collections": 0}

        # Collected before the loop: iterating a cursor while deleting out from
        # under it is not something either driver promises to survive.
        chats = [c async for c in chat_model.iter_user_chats(user_id)]

        for chat in chats:
            chat_id = chat.chat_id

            # --- vectors ---
            collection = self.nlp_for_chat(chat).collection_name(chat_id)
            if await db.vectors().collection_exists(collection):
                if await db.vectors().delete_collection(collection):
                    removed["collections"] += 1

            # --- chunks and assets, via the project the chat's documents sit in ---
            # A notebook nobody uploaded to has no project row, and that is
            # normal rather than an error: there is nothing under it to remove.
            try:
                project = await project_model.get_project(chat_id)
            except ProjectNotFoundError:
                project = None

            if project is not None:
                # Chunks key on the project's row id, not its business id.
                removed["chunks"] += await ChunkModel(db).count_project_chunks(project.id)
                await ChunkModel(db).delete_chunks_for_project(project.id)

                removed["assets"] += len([a async for a in AssetModel(db).iter_assets_for_projects([chat_id])])
                await AssetModel(db).delete_assets_for_project(chat_id)
                await project_model.delete_project(chat_id)

            # --- the conversation, then the chat itself ---
            removed["messages"] += await MessageModel(db).delete_messages_for_chat(chat_id)
            if await chat_model.delete_chat(chat_id):
                removed["chats"] += 1

        removed["sessions"] = await SessionModel(db).delete_sessions_for_user(user_id)
        await self.users.delete_user(user_id)

        self.logger.info(
            "Deleted user %r (%s): %d chat(s), %d session(s), %d asset(s), %d chunk(s), "
            "%d message(s), %d vector collection(s)",
            user_id,
            user.label,
            removed["chats"],
            removed["sessions"],
            removed["assets"],
            removed["chunks"],
            removed["messages"],
            removed["collections"],
        )
        return user, removed
