import uuid
from collections.abc import Callable
from dataclasses import dataclass

from data.models import Artifact, ArtifactModel, ChatModel, ChunkModel, ProjectModel
from shared.enums import ArtifactKind, ArtifactStatus
from shared.exceptions import (
    CELERY_BROKER_EXCEPTIONS,
    CeleryBrokerError,
    InvalidInputError,
    NotFoundError,
)

from ..core import BaseService, IdempotencyService


@dataclass(frozen=True)
class StartedGeneration:
    task_id: str
    #: None when the run was already in flight and nothing new was created.
    artifact_id: str | None
    already_running: bool


class StudioService(BaseService):
    """Starting a generation run, and reading back whatever it has written so far.

    The task itself is handed in as `enqueue` (and its name as `task_name`),
    because the tasks import the services and a service importing them back
    would be a cycle.
    """

    def __init__(self, db, task_name: str, enqueue: Callable[[list], object], on_queued: Callable[[str], None]):
        super().__init__()
        self.db = db
        self.task_name = task_name
        self.enqueue = enqueue
        self.on_queued = on_queued
        self.artifacts = ArtifactModel(db)

    async def find(self, chat_id: str, kind: ArtifactKind) -> Artifact | None:
        return await self.artifacts.find_artifact(chat_id, kind.value)

    async def start(self, chat_id: str, kind: ArtifactKind) -> StartedGeneration:
        """Create the row to poll, then queue the task.

        Raises ChatNotFoundError -> 404 if the notebook does not exist, before
        anything is queued.
        """
        db = self.db
        await ChatModel(db).get_chat(chat_id)

        # Answered here rather than inside the task, because a task that dies
        # on its first line is the worst possible way to say "this notebook has
        # no documents yet": the browser is left polling an artifact that was
        # never created, which it cannot tell apart from one still being
        # written, so the panel says "reading the documents..." forever.
        # Clicking a Studio tile while the upload is still indexing is an
        # ordinary thing to do and gets an ordinary answer.
        try:
            project = await ProjectModel(db).get_project(chat_id)
            chunk_count = await ChunkModel(db).count_project_chunks(project.id)
        except NotFoundError:
            chunk_count = 0

        if not chunk_count:
            raise InvalidInputError(
                f"Notebook {chat_id!r} has nothing to generate from yet. "
                "Add a document and let it finish indexing first."
            )

        idempotency = IdempotencyService(db)
        args = {"chat_id": chat_id, "kind": kind.value}

        running = await idempotency.claim(self.task_name, args)

        if running is not None:
            return StartedGeneration(task_id=running.task_id, artifact_id=None, already_running=True)

        # The row is created *here*, before the task is queued, so that from
        # the moment this returns there is something for the browser to poll.
        # The task used to create it, which left a window -- and, when the task
        # failed early, a permanent hole -- where GET answered `exists: false`
        # and the UI read that as "still working". Creating it upserts on
        # (chat_id, kind) and clears the items, which is exactly what
        # regenerating wants.
        #
        # Reuse the id if this notebook already has a set of this kind. The row
        # is an upsert on (chat_id, kind), so that pair is the real identity and
        # a fresh surrogate id on every regeneration would name the same row
        # differently each time for no reader's benefit.
        previous = await self.artifacts.find_artifact(chat_id, kind.value)
        artifact_id = previous.artifact_id if previous else str(uuid.uuid4())

        # Resets the items, which is what regenerating means.
        await self.artifacts.create_artifact(
            Artifact(
                artifact_id=artifact_id,
                chat_id=chat_id,
                kind=kind,
                status=ArtifactStatus.GENERATING,
            )
        )

        try:
            result = self.enqueue([chat_id, kind.value])
        except CELERY_BROKER_EXCEPTIONS as exc:
            # The row exists now, so it has to be marked rather than left
            # claiming to be generating something nothing is working on.
            await self.artifacts.finish_artifact(artifact_id, ArtifactStatus.FAILED.value, "could not be queued")
            raise CeleryBrokerError(f"Could not queue {kind.value} generation for {chat_id!r}") from exc

        self.on_queued(result.id)
        await idempotency.record(task_id=result.id, task_name=self.task_name, project_id=chat_id, args=args)

        return StartedGeneration(task_id=result.id, artifact_id=artifact_id, already_running=False)
