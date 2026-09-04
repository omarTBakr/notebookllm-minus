from typing import AsyncIterator

from motor.motor_asyncio import AsyncIOMotorClient  # ty: ignore[unresolved-import]
from pymongo import ReturnDocument  # ty: ignore[unresolved-import]
from pymongo.errors import PyMongoError  # ty: ignore[unresolved-import]

from enums import IN_FLIGHT, DatabaseCollection, TaskExecutionStatus
from exceptions import DbError, NotFoundError
from models.db_schema import TaskExecution
from models.db_schema.project import utcnow

from ..interfaces.task_repository import TaskRepository
from .base_model import BaseModel

_IN_FLIGHT = [status.value for status in IN_FLIGHT]

_TERMINAL = (
    TaskExecutionStatus.SUCCESS.value,
    TaskExecutionStatus.FAILURE.value,
    TaskExecutionStatus.DEAD.value,
)


class MongoTaskRepository(TaskRepository, BaseModel):
    """Data access for the task_executions collection."""

    def __init__(self, db: AsyncIOMotorClient):
        super().__init__(db, DatabaseCollection.TASK_EXECUTIONS)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_task(self, task: TaskExecution) -> str:
        document = task.model_dump(by_alias=True)

        # Upsert rather than insert: a worker can start a task before the
        # publishing request has committed its row, so whichever write arrives
        # second must update rather than raise a duplicate key error.
        # $setOnInsert keeps the original _id and created_at when it does.
        identity = {"_id": document.pop("_id"), "created_at": document.pop("created_at")}

        try:
            await self.collection.find_one_and_update(
                {"task_id": task.task_id},
                {"$set": document, "$setOnInsert": identity},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            raise DbError(f"Could not create task execution {task.task_id!r}") from exc

        return task.task_id

    async def update_status(
        self,
        task_id: str,
        status: str,
        result: dict | None = None,
        error: str = "",
        error_type: str = "",
    ) -> None:
        now = utcnow()

        changes: dict = {"status": status, "updated_at": now}

        # Set here rather than at the call sites so neither timestamp can be
        # forgotten by one caller and set by another.
        if status == TaskExecutionStatus.STARTED.value:
            changes["started_at"] = now

        if status in _TERMINAL:
            changes["completed_at"] = now

        if result is not None:
            changes["result"] = result

        if error:
            changes["error"] = error[:2000]
            changes["error_type"] = error_type

        try:
            await self.collection.update_one({"task_id": task_id}, {"$set": changes})
        except PyMongoError as exc:
            raise DbError(f"Could not update task {task_id!r}") from exc

    async def set_stage(self, task_id: str, stage: str, done: int = 0, total: int = 0) -> None:
        try:
            await self.collection.update_one(
                {"task_id": task_id},
                {"$set": {"stage": stage, "done": done, "total": total, "updated_at": utcnow()}},
            )
        except PyMongoError as exc:
            raise DbError(f"Could not set stage for task {task_id!r}") from exc

    async def delete_finished_before(self, cutoff) -> int:
        try:
            result = await self.collection.delete_many(
                {"status": {"$in": list(_TERMINAL)}, "created_at": {"$lt": cutoff}}
            )
        except PyMongoError as exc:
            raise DbError("Could not sweep finished tasks") from exc

        return result.deleted_count

    async def mark_abandoned(self, started_before, queued_before, status: str) -> int:
        now = utcnow()

        try:
            result = await self.collection.update_many(
                {
                    "$or": [
                        {
                            "status": TaskExecutionStatus.STARTED.value,
                            "started_at": {"$lt": started_before},
                        },
                        {
                            "status": TaskExecutionStatus.QUEUED.value,
                            "created_at": {"$lt": queued_before},
                        },
                    ]
                },
                {
                    "$set": {
                        "status": status,
                        "error": "no completion recorded; the worker running this task is gone",
                        "error_type": "WorkerLost",
                        "completed_at": now,
                        "updated_at": now,
                    }
                },
            )
        except PyMongoError as exc:
            raise DbError("Could not mark stale tasks") from exc

        return result.modified_count

    async def delete_tasks_for_project(self, project_id: str) -> None:
        try:
            await self.collection.delete_many({"project_id": project_id})
        except PyMongoError as exc:
            raise DbError(f"Could not delete tasks for project {project_id!r}") from exc

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_task(self, task_id: str) -> TaskExecution:
        task = await self.find_task(task_id)

        if task is None:
            raise NotFoundError(f"Task {task_id!r} not found")

        return task

    async def find_task(self, task_id: str) -> TaskExecution | None:
        try:
            record = await self.collection.find_one({"task_id": task_id})
        except PyMongoError as exc:
            raise DbError(f"Could not look up task {task_id!r}") from exc

        return TaskExecution(**record) if record else None

    async def find_in_flight(self, task_name: str, args_hash: str) -> TaskExecution | None:
        # Never match on the empty sentinel: every unhashed row shares it, so
        # without this guard the first unhashed task would absorb every later
        # submission of anything.
        if not args_hash:
            return None

        try:
            record = await self.collection.find_one(
                {
                    "task_name": task_name,
                    "args_hash": args_hash,
                    "status": {"$in": _IN_FLIGHT},
                },
                sort=[("created_at", -1)],
            )
        except PyMongoError as exc:
            raise DbError("Could not look up in-flight task") from exc

        return TaskExecution(**record) if record else None

    async def find_active_for_project(self, project_id: str) -> TaskExecution | None:
        try:
            record = await self.collection.find_one(
                {"project_id": project_id, "status": {"$in": _IN_FLIGHT}},
                sort=[("created_at", -1)],
            )
        except PyMongoError as exc:
            raise DbError(f"Could not look up active task for {project_id!r}") from exc

        return TaskExecution(**record) if record else None

    async def iter_project_tasks(self, project_id: str) -> AsyncIterator[TaskExecution]:
        try:
            cursor = self.collection.find({"project_id": project_id}).sort("created_at", -1)

            async for record in cursor:
                yield TaskExecution(**record)

        except PyMongoError as exc:
            raise DbError(f"Could not list tasks for project {project_id!r}") from exc
