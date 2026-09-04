from abc import ABC, abstractmethod
from typing import AsyncIterator

from models.db_schema import TaskExecution


class TaskRepository(ABC):
    @abstractmethod
    async def create_task(self, task: TaskExecution) -> str:
        pass

    @abstractmethod
    async def get_task(self, task_id: str) -> TaskExecution:
        """One task by its Celery id. Raises when there is none."""
        pass

    @abstractmethod
    async def find_task(self, task_id: str) -> TaskExecution | None:
        """One task by its Celery id, or None.

        The or-None twin of get_task, for the status endpoint: being asked
        about an id that was never queued is an ordinary answer there, not an
        exception.
        """
        pass

    @abstractmethod
    async def find_in_flight(self, task_name: str, args_hash: str) -> TaskExecution | None:
        """A queued-or-running task with these exact arguments, or None.

        Must not match finished work: re-running an ingestion that already
        completed is a legitimate request, and only work still outstanding may
        absorb a duplicate submission.

        Must return None for an empty args_hash rather than matching every
        other unhashed row — the same guard find_by_content_hash uses.
        """
        pass

    @abstractmethod
    async def find_active_for_project(self, project_id: str) -> TaskExecution | None:
        """The project's most recent unfinished task, or None.

        What the browser's progress poll reads. Most recent rather than
        oldest: a second upload while one is running is the state the user is
        actually looking at.
        """
        pass

    @abstractmethod
    async def update_status(
        self,
        task_id: str,
        status: str,
        result: dict | None = None,
        error: str = "",
        error_type: str = "",
    ) -> None:
        """Move a task to a terminal or running state.

        Sets started_at on the first move to STARTED and completed_at on any
        terminal status, so neither timestamp can be forgotten at a call site.
        """
        pass

    @abstractmethod
    async def set_stage(self, task_id: str, stage: str, done: int = 0, total: int = 0) -> None:
        """Record ingestion progress. Called often; must stay cheap."""
        pass

    @abstractmethod
    async def iter_project_tasks(self, project_id: str) -> AsyncIterator[TaskExecution]:
        """Every task for one project, newest first, unpaginated."""

    @abstractmethod
    async def delete_finished_before(self, cutoff) -> int:
        """Drop finished rows older than *cutoff*. Returns how many went.

        Only terminal rows: deleting something still queued or running would
        lose the record of work that is currently happening.
        """
        pass

    @abstractmethod
    async def mark_abandoned(self, started_before, queued_before, status: str) -> int:
        """Move work that can no longer be running to a terminal state.

        Two cutoffs, because the two states go wrong differently and on very
        different timescales:

        *started_before* catches a worker killed mid-task. It tells nobody, so
        its row stays STARTED for good — the one state the table cannot correct
        on its own, since the process that would have written the ending is
        gone. The cutoff can be tight: nothing may run longer than the hard
        time limit.

        *queued_before* catches work that was published and never picked up.
        This cutoff must be generous, because a task legitimately waits as long
        as its workers are down, and marking those DEAD during a deploy would
        report real pending work as lost.
        """
        pass

    @abstractmethod
    async def delete_tasks_for_project(self, project_id: str) -> None:
        """Drop a project's task history, for when the project itself goes."""
        pass
