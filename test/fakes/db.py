"""An in-memory DbProvider.

`models/__init__.py` is a thin adapter — `AssetModel(db)` is literally
`db.assets()` — and every route reaches storage through `request.app.db`.
So one fake provider with nine accessors covers the whole route layer, with
no patching and no mongomock.

Repositories here store pydantic models in dicts and raise the same typed
errors the real ones do, because the status code a route returns is derived
from the exception class.
"""

from datetime import datetime, timezone

from enums import IN_FLIGHT, TaskExecutionStatus
from exceptions import (
    AssetNotFoundError,
    ChatNotFoundError,
    NotFoundError,
    ProjectNotFoundError,
    SessionNotFoundError,
    UserNotFoundError,
)


class _Store:
    """Shared plumbing: a dict keyed by the model's business id."""

    key: str = ""
    missing: type[Exception] = KeyError

    def __init__(self):
        self.items: dict[str, object] = {}

    def _get(self, ident: str):
        try:
            return self.items[ident]
        except KeyError:
            raise self.missing(f"{ident!r} not found") from None

    def _patch(self, ident: str, **changes):
        item = self._get(ident)
        for field, value in changes.items():
            if value is not None:
                setattr(item, field, value)
        return item


class FakeUserRepository(_Store):
    missing = UserNotFoundError

    async def create_user(self, user):
        self.items[user.user_id] = user
        return str(user.id)

    async def get_user(self, user_id):
        return self._get(user_id)

    async def rename(self, user_id, label):
        self._patch(user_id, label=label)

    async def delete_user(self, user_id):
        return self.items.pop(user_id, None) is not None

    async def count_users(self):
        return len(self.items)

    async def iter_users(self):
        for user in list(self.items.values()):
            yield user


class FakeSessionRepository(_Store):
    missing = SessionNotFoundError

    async def create_session(self, session):
        self.items[session.session_id] = session
        return str(session.id)

    async def get_session(self, session_id):
        return self._get(session_id)

    async def delete_sessions_for_user(self, user_id):
        gone = [k for k, x in self.items.items() if x.user_id == user_id]
        for k in gone:
            del self.items[k]
        return len(gone)

    async def iter_user_sessions(self, user_id):
        for s in list(self.items.values()):
            if s.user_id == user_id:
                yield s


class FakeChatRepository(_Store):
    missing = ChatNotFoundError

    async def create_chat(self, chat):
        self.items[chat.chat_id] = chat
        return str(chat.id)

    async def get_chat(self, chat_id):
        return self._get(chat_id)

    async def rename(self, chat_id, title):
        self._patch(chat_id, title=title)

    async def set_has_documents(self, chat_id, value):
        self._patch(chat_id, has_documents=value)

    async def set_models(self, chat_id, generation_model=None, embedding_model=None,
                         embedding_dimensions=None):
        self._patch(chat_id, generation_model=generation_model,
                    embedding_model=embedding_model,
                    embedding_dimensions=embedding_dimensions)

    async def set_settings(self, chat_id, changes):
        self._patch(chat_id, **changes)

    async def delete_chat(self, chat_id):
        return self.items.pop(chat_id, None) is not None

    async def iter_user_chats(self, user_id):
        for c in list(self.items.values()):
            if c.user_id == user_id:
                yield c

    async def iter_session_chats(self, session_id):
        for c in list(self.items.values()):
            if c.session_id == session_id:
                yield c


class FakeMessageRepository:
    def __init__(self):
        self.items: list = []

    async def create_message(self, message):
        self.items.append(message)
        return str(message.id)

    async def iter_chat_messages(self, chat_id):
        for m in [m for m in self.items if m.chat_id == chat_id]:
            yield m

    async def delete_messages_for_chat(self, chat_id):
        before = len(self.items)
        self.items = [m for m in self.items if m.chat_id != chat_id]
        return before - len(self.items)

    async def get_recent_history(self, chat_id, limit):
        turns = [m for m in self.items if m.chat_id == chat_id]
        return [{"role": m.role.value, "content": m.content} for m in turns[-limit:]]


class FakeProjectRepository(_Store):
    missing = ProjectNotFoundError

    async def create_project(self, project):
        self.items[project.project_id] = project
        return str(project.id)

    async def update_project(self, project):
        """Upsert, returning the row's ObjectId — what DataChunk.project_id is."""
        self.items.setdefault(project.project_id, project)
        return self.items[project.project_id].id

    async def get_project(self, project_id):
        return self._get(project_id)

    async def rename(self, project_id, name):
        self._patch(project_id, name=name)

    async def delete_project(self, project_id):
        if self.items.pop(project_id, None) is None:
            raise ProjectNotFoundError(f"{project_id!r} not found")

    async def add_asset_id(self, project_id, asset_object_id):
        self._get(project_id).assets_ids.append(asset_object_id)

    async def add_chunk_ids(self, project_id, chunk_object_ids):
        self._get(project_id).chunks_ids.extend(chunk_object_ids)


class FakeAssetRepository(_Store):
    missing = AssetNotFoundError

    async def create_asset(self, asset):
        self.items[asset.asset_id] = asset
        return str(asset.id)

    async def update_asset(self, asset):
        self.items[asset.asset_id] = asset
        return str(asset.id)

    async def get_asset(self, asset_id):
        return self._get(asset_id)

    async def rename(self, asset_id, name):
        self._patch(asset_id, name=name)

    async def find_by_content_hash(self, project_id, content_hash):
        if not content_hash:
            return None
        return next(
            (
                a
                for a in self.items.values()
                if a.project_id == project_id and a.content_hash == content_hash
            ),
            None,
        )

    async def delete_asset(self, asset_id):
        return self.items.pop(asset_id, None) is not None

    async def iter_assets_for_projects(self, project_ids):
        wanted = set(project_ids)
        for a in list(self.items.values()):
            if a.project_id in wanted:
                yield a

    async def delete_assets_for_project(self, project_id):
        gone = [k for k, a in self.items.items() if a.project_id == project_id]
        for k in gone:
            del self.items[k]
        return len(gone)


class FakeChunkRepository:
    def __init__(self):
        self.items: list = []

    async def create_chunks(self, chunks):
        self.items.extend(chunks)
        return [str(c.id) for c in chunks]

    async def iter_chunks(self, asset_id):
        for c in [c for c in self.items if c.asset_id == asset_id]:
            yield c

    async def iter_project_chunks(self, project_id, asset_id=None):
        for c in self.items:
            if str(c.project_id) != str(project_id):
                continue
            if asset_id is not None and c.asset_id != asset_id:
                continue
            yield c

    async def count_project_chunks(self, project_id, asset_id=None):
        # asset_id mirrors the real repositories, which scope the count the
        # same way iter_project_chunks scopes its walk. The fake was missing
        # it, so an asset-scoped count raised TypeError rather than returning
        # a number — invisible until something actually passed one.
        return sum(
            1
            for c in self.items
            if str(c.project_id) == str(project_id)
            and (asset_id is None or c.asset_id == asset_id)
        )

    async def has_asset_chunks(self, project_id, asset_id):
        # project_id mirrors the interface and both real backends. The fake
        # took only asset_id, so a call with both raised TypeError — the same
        # drift count_project_chunks had above.
        return any(
            c.asset_id == asset_id and str(c.project_id) == str(project_id)
            for c in self.items
        )

    async def get_chunks_by_orders(self, asset_id, chunk_orders):
        wanted = set(chunk_orders)
        return {
            c.chunk_order: c
            for c in self.items
            if c.asset_id == asset_id and c.chunk_order in wanted
        }

    async def delete_chunks_for_project(self, project_id):
        before = len(self.items)
        self.items = [c for c in self.items if str(c.project_id) != str(project_id)]
        return before - len(self.items)

    async def delete_chunks_for_asset(self, project_id, asset_id):
        # Returns the removed ids, as both real backends do — the caller pulls
        # exactly those out of the project's chunks_ids.
        gone = [
            c
            for c in self.items
            if c.asset_id == asset_id and str(c.project_id) == str(project_id)
        ]
        self.items = [c for c in self.items if c not in gone]
        return [str(c.id) for c in gone]


class FakeVectorRepository:
    """Records what was indexed; search returns whatever was staged."""

    def __init__(self, hits=None):
        self.collections: dict[str, dict] = {}
        self.points: dict[str, list[dict]] = {}
        self.hits = hits if hits is not None else []
        self.searched: list[dict] = []
        self.indexed: list[dict] = []

    async def collection_exists(self, collection_name):
        return collection_name in self.collections

    async def list_collections(self):
        return list(self.collections)

    async def get_collection_info(self, collection_name):
        return {"name": collection_name, "points_count": len(self.points.get(collection_name, []))}

    async def create_collection(self, collection_name, embedding_size, reset=False):
        existed = collection_name in self.collections
        if reset or not existed:
            self.collections[collection_name] = {"embedding_size": embedding_size}
            self.points[collection_name] = []
        return not existed or reset

    async def create_index(
        self, collection_name, embedding_size, index_type=None, reset=False
    ):
        """Records the build; both real backends do it after the bulk load.

        Absent until now, which every upload test hit as a bare 500 —
        NLPController.index_chunks() has called this since the ANN build moved
        after insertion, and a fake that stops matching the interface fails in
        the one place that says nothing about why.
        """
        self.indexed.append(
            {
                "collection_name": collection_name,
                "embedding_size": embedding_size,
                "index_type": index_type,
                "reset": reset,
            }
        )
        return True

    async def delete_collection(self, collection_name):
        # The points go with it. The real backends drop the table (Postgres) or
        # the collection (Qdrant), so a fake that kept them would let a test
        # pass while vectors outlived whatever owned them.
        existed = self.collections.pop(collection_name, None) is not None
        self.points.pop(collection_name, None)
        return existed

    async def insert_many(self, collection_name, texts, vectors, metadata=None,
                          record_ids=None, batch_size=64):
        rows = self.points.setdefault(collection_name, [])
        for i, text in enumerate(texts):
            rows.append({
                "id": record_ids[i] if record_ids else str(i),
                "text": text,
                "vector": vectors[i],
                "metadata": (metadata or [{}] * len(texts))[i],
            })
        return True

    async def delete_by_metadata(self, collection_name, key, value):
        rows = self.points.get(collection_name, [])
        keep = [r for r in rows if (r["metadata"] or {}).get(key) != value]
        removed = len(rows) - len(keep)
        self.points[collection_name] = keep
        return removed

    async def search_by_vector(self, collection_name, vector, limit=5, asset_ids=None):
        self.searched.append({"collection": collection_name, "limit": limit,
                              "asset_ids": asset_ids})
        return self.hits[:limit]



class FakeTaskRepository:
    """In-memory task_executions, keyed by Celery task id."""

    def __init__(self):
        self.items: dict[str, object] = {}

    async def create_task(self, task):
        self.items[task.task_id] = task
        return task.task_id

    async def get_task(self, task_id):
        task = self.items.get(task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id!r} not found")
        return task

    async def find_task(self, task_id):
        return self.items.get(task_id)

    async def find_in_flight(self, task_name, args_hash):
        # The empty-hash guard is part of the contract, not an optimisation:
        # without it every unhashed row would match every other one.
        if not args_hash:
            return None
        return next(
            (
                t
                for t in sorted(self.items.values(), key=lambda t: t.created_at, reverse=True)
                if t.task_name == task_name
                and t.args_hash == args_hash
                and t.status in IN_FLIGHT
            ),
            None,
        )

    async def find_active_for_project(self, project_id):
        return next(
            (
                t
                for t in sorted(self.items.values(), key=lambda t: t.created_at, reverse=True)
                if t.project_id == project_id and t.status in IN_FLIGHT
            ),
            None,
        )

    async def update_status(self, task_id, status, result=None, error="", error_type=""):
        task = self.items.get(task_id)
        if task is None:
            return
        task.status = TaskExecutionStatus(status)
        now = datetime.now(timezone.utc)
        if status == TaskExecutionStatus.STARTED.value:
            task.started_at = now
        if status in (
            TaskExecutionStatus.SUCCESS.value,
            TaskExecutionStatus.FAILURE.value,
            TaskExecutionStatus.DEAD.value,
        ):
            task.completed_at = now
        if result is not None:
            task.result = result
        if error:
            task.error = error[:2000]
            task.error_type = error_type
        task.updated_at = now

    async def set_stage(self, task_id, stage, done=0, total=0):
        task = self.items.get(task_id)
        if task is None:
            return
        task.stage, task.done, task.total = stage, done, total

    async def iter_project_tasks(self, project_id):
        for task in sorted(self.items.values(), key=lambda t: t.created_at, reverse=True):
            if task.project_id == project_id:
                yield task

    async def delete_finished_before(self, cutoff):
        terminal = {
            TaskExecutionStatus.SUCCESS,
            TaskExecutionStatus.FAILURE,
            TaskExecutionStatus.DEAD,
        }
        doomed = [
            k for k, v in self.items.items()
            if v.status in terminal and v.created_at < cutoff
        ]
        for k in doomed:
            del self.items[k]
        return len(doomed)

    async def mark_abandoned(self, started_before, queued_before, status):
        marked = 0
        for task in self.items.values():
            stale_run = (
                task.status is TaskExecutionStatus.STARTED
                and task.started_at
                and task.started_at < started_before
            )
            stale_queue = (
                task.status is TaskExecutionStatus.QUEUED and task.created_at < queued_before
            )
            if stale_run or stale_queue:
                task.status = TaskExecutionStatus(status)
                task.error = "no completion recorded; the worker running this task is gone"
                task.error_type = "WorkerLost"
                task.completed_at = datetime.now(timezone.utc)
                marked += 1
        return marked

    async def delete_tasks_for_project(self, project_id):
        self.items = {
            k: v for k, v in self.items.items() if v.project_id != project_id
        }


class FakeDb:
    """A DbProvider built from the repositories above."""

    def __init__(self, hits=None):
        self._users = FakeUserRepository()
        self._sessions = FakeSessionRepository()
        self._chats = FakeChatRepository()
        self._messages = FakeMessageRepository()
        self._projects = FakeProjectRepository()
        self._assets = FakeAssetRepository()
        self._chunks = FakeChunkRepository()
        self._vectors = FakeVectorRepository(hits=hits)
        self._tasks = FakeTaskRepository()
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def setup_indexes(self):
        return None

    def users(self):    return self._users
    def sessions(self): return self._sessions
    def chats(self):    return self._chats
    def messages(self): return self._messages
    def projects(self): return self._projects
    def assets(self):   return self._assets
    def chunks(self):   return self._chunks
    def vectors(self):  return self._vectors
    def tasks(self):    return self._tasks
