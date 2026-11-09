"""Who owns a resource: the single ownership check behind every route.

Every id a route takes resolves to exactly one owning user:

    user_id                 itself
    session_id   -> session.user_id
    chat_id      -> chat.user_id
    project_id   -> the chat with that id (a chat id *is* a project id)
    task_id      -> task.project_id -> that chat

The presentation tier's `enforce_ownership` dependency calls `check` with a
route's path parameters before the route runs. A resource that does not exist
and one that belongs to someone else raise the same 404, so the response never
confirms that another user's id exists.
"""

from shared.exceptions import (
    ChatNotFoundError,
    NotFoundError,
    ProjectNotFoundError,
    SessionNotFoundError,
    UserNotFoundError,
)

from ..core import BaseService


class AccessService(BaseService):
    def __init__(self, db) -> None:
        super().__init__()
        self.db = db

    async def check(self, user_id: str, params: dict[str, str]) -> None:
        """Raise NotFoundError unless *user_id* owns every id in *params*."""
        if "user_id" in params and params["user_id"] != user_id:
            raise UserNotFoundError("User not found")

        if "session_id" in params:
            await self._own_session(user_id, params["session_id"])

        if "chat_id" in params:
            await self._own_chat(user_id, params["chat_id"], ChatNotFoundError)

        if "project_id" in params:
            await self._own_chat(user_id, params["project_id"], ProjectNotFoundError)

        if "task_id" in params:
            await self._own_task(user_id, params["task_id"])

    async def owns_chat(self, user_id: str, chat_id: str) -> bool:
        try:
            await self._own_chat(user_id, chat_id, ChatNotFoundError)
        except NotFoundError:
            return False
        return True

    async def _own_session(self, user_id: str, session_id: str) -> None:
        try:
            session = await self.db.sessions().get_session(session_id)
        except SessionNotFoundError:
            session = None
        if session is None or session.user_id != user_id:
            raise SessionNotFoundError("Session not found")

    async def _own_chat(self, user_id: str, chat_id: str, missing: type[NotFoundError]) -> None:
        try:
            chat = await self.db.chats().get_chat(chat_id)
        except ChatNotFoundError:
            chat = None
        if chat is None or chat.user_id != user_id:
            # A project with no notebook over it (made through the old /data
            # route) has no owner, so nobody may reach it.
            raise missing(f"{missing.__name__.removesuffix('NotFoundError')} not found")

    async def _own_task(self, user_id: str, task_id: str) -> None:
        task = await self.db.tasks().find_task(task_id)
        if task is None or not task.project_id or not await self.owns_chat(user_id, task.project_id):
            raise NotFoundError("Task not found")
