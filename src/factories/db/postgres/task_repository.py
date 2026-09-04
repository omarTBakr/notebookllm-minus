from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from enums import IN_FLIGHT, TaskExecutionStatus
from exceptions import DbError, NotFoundError
from models.db_schema import TaskExecution

from ..interfaces.task_repository import TaskRepository
from .base_repository import PostgresBaseRepository, TaskExecutionRow

_IN_FLIGHT = [status.value for status in IN_FLIGHT]

_TERMINAL = (
    TaskExecutionStatus.SUCCESS.value,
    TaskExecutionStatus.FAILURE.value,
    TaskExecutionStatus.DEAD.value,
)


class PostgresTaskRepository(PostgresBaseRepository, TaskRepository):
    """PostgreSQL implementation of TaskRepository."""

    async def create_task(self, task: TaskExecution) -> str:
        # An upsert on task_id rather than a plain insert: a worker can begin
        # a task before the publishing request has committed its row, so
        # whichever arrives second must not raise a duplicate-key error.
        statement = insert(TaskExecutionRow).values(
            id=self._generate_id(),
            task_id=task.task_id,
            task_name=task.task_name,
            project_id=task.project_id,
            asset_id=task.asset_id,
            status=task.status.value,
            stage=task.stage,
            done=task.done,
            total=task.total,
            args=self._scrub(task.args),
            args_hash=task.args_hash,
            result=self._scrub(task.result),
            error=task.error,
            error_type=task.error_type,
            started_at=task.started_at,
            completed_at=task.completed_at,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["task_id"],
            set_={
                "status": statement.excluded.status,
                "updated_at": statement.excluded.updated_at,
            },
        )

        try:
            async with self.session_factory.begin() as db:
                await db.execute(statement)
            return task.task_id
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create task execution: {exc}") from exc

    async def get_task(self, task_id: str) -> TaskExecution:
        task = await self.find_task(task_id)

        if task is None:
            raise NotFoundError(f"Task {task_id!r} not found")

        return task

    async def find_task(self, task_id: str) -> TaskExecution | None:
        try:
            async with self.session_factory() as db:
                result = await db.execute(select(TaskExecutionRow).where(TaskExecutionRow.task_id == task_id))
                row = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to look up task {task_id!r}: {exc}") from exc

        return self._to_model(row)

    async def find_in_flight(self, task_name: str, args_hash: str) -> TaskExecution | None:
        # Never match on the empty sentinel: every unhashed row shares it, so
        # without this guard the first unhashed task would absorb every later
        # submission of anything.
        if not args_hash:
            return None

        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(TaskExecutionRow)
                    .where(
                        TaskExecutionRow.task_name == task_name,
                        TaskExecutionRow.args_hash == args_hash,
                        TaskExecutionRow.status.in_(_IN_FLIGHT),
                    )
                    .order_by(TaskExecutionRow.created_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to look up in-flight task: {exc}") from exc

        return self._to_model(row)

    async def find_active_for_project(self, project_id: str) -> TaskExecution | None:
        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    select(TaskExecutionRow)
                    .where(
                        TaskExecutionRow.project_id == project_id,
                        TaskExecutionRow.status.in_(_IN_FLIGHT),
                    )
                    .order_by(TaskExecutionRow.created_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to look up active task: {exc}") from exc

        return self._to_model(row)

    async def update_status(
        self,
        task_id: str,
        status: str,
        result: dict | None = None,
        error: str = "",
        error_type: str = "",
    ) -> None:
        now = datetime.now(timezone.utc)

        values: dict = {"status": status, "updated_at": now}

        # Set here rather than at the call sites so neither timestamp can be
        # forgotten by one caller and set by another.
        if status == TaskExecutionStatus.STARTED.value:
            values["started_at"] = now

        if status in _TERMINAL:
            values["completed_at"] = now

        if result is not None:
            values["result"] = self._scrub(result)

        if error:
            values["error"] = error[:2000]
            values["error_type"] = error_type

        try:
            async with self.session_factory.begin() as db:
                await db.execute(update(TaskExecutionRow).where(TaskExecutionRow.task_id == task_id).values(**values))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to update task {task_id!r}: {exc}") from exc

    async def set_stage(self, task_id: str, stage: str, done: int = 0, total: int = 0) -> None:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(
                    update(TaskExecutionRow)
                    .where(TaskExecutionRow.task_id == task_id)
                    .values(
                        stage=stage,
                        done=done,
                        total=total,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to set stage for task {task_id!r}: {exc}") from exc

    async def iter_project_tasks(self, project_id: str) -> AsyncIterator[TaskExecution]:
        try:
            async with self.session_factory() as db:
                stream = await db.stream_scalars(
                    select(TaskExecutionRow)
                    .where(TaskExecutionRow.project_id == project_id)
                    .order_by(TaskExecutionRow.created_at.desc())
                )
                async for row in stream:
                    yield self._to_model(row)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to list tasks for project {project_id!r}: {exc}") from exc

    async def delete_finished_before(self, cutoff) -> int:
        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    delete(TaskExecutionRow).where(
                        TaskExecutionRow.status.in_(_TERMINAL),
                        TaskExecutionRow.created_at < cutoff,
                    )
                )
            return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to sweep finished tasks: {exc}") from exc

    async def mark_abandoned(self, started_before, queued_before, status: str) -> int:
        now = datetime.now(timezone.utc)

        try:
            async with self.session_factory.begin() as db:
                result = await db.execute(
                    update(TaskExecutionRow)
                    .where(
                        or_(
                            and_(
                                TaskExecutionRow.status == TaskExecutionStatus.STARTED.value,
                                TaskExecutionRow.started_at < started_before,
                            ),
                            and_(
                                TaskExecutionRow.status == TaskExecutionStatus.QUEUED.value,
                                TaskExecutionRow.created_at < queued_before,
                            ),
                        )
                    )
                    .values(
                        status=status,
                        error="no completion recorded; the worker running this task is gone",
                        error_type="WorkerLost",
                        completed_at=now,
                        updated_at=now,
                    )
                )
            return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to mark stale tasks: {exc}") from exc

    async def delete_tasks_for_project(self, project_id: str) -> None:
        try:
            async with self.session_factory.begin() as db:
                await db.execute(delete(TaskExecutionRow).where(TaskExecutionRow.project_id == project_id))
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete tasks for {project_id!r}: {exc}") from exc

    def _to_model(self, row) -> TaskExecution | None:
        """One ORM row as the shared pydantic model, or None.

        Private, so the backend-parity test does not demand a twin on Mongo,
        where documents already arrive in the model's own shape.
        """
        if row is None:
            return None

        return self._record_to_model(row.__dict__, TaskExecution)
