"""Putting one document into a notebook, and getting its ingestion queued.

Two policies, and neither of them is HTTP.

**Is this document already here?** Identity is the bytes, not the name — but a
copy that got in and a copy whose ingestion died leave the same row behind, and
only one of them is a duplicate. Telling them apart is `store`.

**Is it queued and recorded?** A chain's links each need a task row before the
worker writes to it, and an asset committed with nothing queued behind it is
the husk the first policy then has to recognise. Doing both, and undoing the
asset when the broker refuses, is `queue`.

Both lived inline in ``presentation/routes/chat/assets.py``, where they made
``attach_document`` a 230-line function — most of a route module, in a package
whose whole job is meant to be "what a request means, what comes back". They
are here because they are decisions about a notebook's contents, they need no
request, and each one is worth a test that does not go through a client.
"""

import hashlib
import uuid

from data.models import (
    Asset,
    AssetModel,
    ChunkModel,
    Project,
    ProjectModel,
    TaskModel,
)
from shared.enums import AssetType, TaskExecutionStatus
from shared.exceptions import (
    CELERY_BROKER_EXCEPTIONS,
    CeleryBrokerError,
    DuplicateAssetError,
)

from ..core.BaseService import BaseService
from ..core.IdempotencyService import IdempotencyService


class QueuedIngestion:
    """The two task ids a caller needs to follow one upload.

    Both are read off the *walked* chain rather than by naming ``.parent``
    positionally. ``apply_async`` returns the last link, so ``result.parent``
    meant "the process task" only while the chain was two links long; a third
    was added and that silently started handing the browser the *index* task
    instead — which sits QUEUED for the whole of OCR, so a progress row read
    "Queued" for seven minutes and then jumped to 74%.
    """

    __slots__ = ("task_id", "index_task_id")

    def __init__(self, task_id: str, index_task_id: str) -> None:
        #: The task whose progress a user is waiting on. ``chain_results`` is
        #: documented as oldest-first for exactly this.
        self.task_id = task_id
        self.index_task_id = index_task_id


class AssetIngestService(BaseService):
    """Stores an uploaded document and queues the chain that ingests it."""

    def __init__(self, db) -> None:
        super().__init__()
        self.db = db
        self.assets = AssetModel(db)
        self.projects = ProjectModel(db)
        self.tasks = TaskModel(db)

    async def store(
        self,
        project_id: str,
        *,
        filename: str,
        content_type: str | None,
        file_bytes: bytes,
        project_description: str,
        description: str,
        asset_type: AssetType | None = None,
        content_hash: str | None = None,
        source_url: str = "",
    ) -> Asset:
        """Commit *file_bytes* as an asset of *project_id*, or refuse it.

        Raises :class:`DuplicateAssetError` when the notebook already holds
        these bytes and that copy is real. See :meth:`_is_husk` for when it
        is not.

        The last three are for a source added from a link. *asset_type*
        overrides the one read from *content_type* (a YouTube transcript has no
        MIME type of its own). *content_hash* overrides the hash of the bytes
        where the bytes are not the source's identity: an article's extracted
        text and a video's transcript can change between two fetches of the
        same link, and adding the same link twice is still a duplicate.
        """
        content_hash = content_hash or hashlib.sha256(file_bytes).hexdigest()

        # Identity is the bytes, not the name: asset_id is a fresh uuid every
        # time, and a file can be renamed or saved under another name. Checked
        # before the asset row exists, so a duplicate costs one indexed lookup
        # rather than a full chunk-and-embed that is then discarded.
        existing = await self.assets.find_by_content_hash(project_id, content_hash)

        if existing is not None:
            if not await self._is_husk(project_id, existing.asset_id):
                raise DuplicateAssetError(f"{existing.name!r} is already in this notebook.")

            # Nothing indexed and nothing running: the previous attempt is over
            # and it failed. Drop the husk and let this upload proceed as a
            # first upload. delete_asset only, matching the broker-failure
            # rollback in `queue` and the delete-source route -- nothing in
            # this codebase unlinks an id from project.assets_ids.
            self.logger.info(
                "Replacing %r in project %s: its previous ingestion is recorded " "as failed and left no chunks behind",
                existing.name,
                project_id,
            )
            await self.assets.delete_asset(existing.asset_id)

        # The project row backs the existing chunk/vector plumbing. Called for
        # that side effect only: add_asset_id below needs the row to exist, and
        # the row id it returns is not needed here now that chunking happens on
        # a worker that looks the project up itself.
        await self.projects.update_project(
            Project(
                project_id=project_id,
                name=filename,
                description=project_description,
            )
        )

        asset = Asset(
            asset_id=str(uuid.uuid4()),
            asset_type=asset_type or AssetType.from_content_type(content_type),
            project_id=project_id,
            name=filename,
            description=description,
            file_bytes=file_bytes,
            content_hash=content_hash,
            source_url=source_url,
        )

        asset_object_id = await self.assets.update_asset(asset)
        await self.projects.add_asset_id(project_id, asset_object_id)

        return asset

    async def _is_husk(self, project_id: str, asset_id: str) -> bool:
        """Whether an existing asset is the wreck of an ingestion that died.

        The dedupe above is right about a document that got in. It was wrong
        about one that did not: an ingestion killed part-way — a soft time
        limit, an OOM kill, a worker restart — leaves the asset row committed
        with no chunks behind it, and the hash check then answered 409 to
        every retry of the same file. The document is listed in Sources, the
        model cannot see a word of it, and the only way out is deleting it by
        hand, with nothing anywhere saying so. That is a transient failure
        made permanent by its own error handling. Three uploads of one Arabic
        book hit exactly this on the deployment box: killed at the 540s soft
        limit, three asset rows, zero chunks, 409 on every retry.

        Positive evidence of a failed run is required, not merely an absence
        of chunks. Chunks appear asynchronously, so a copy uploaded seconds
        ago legitimately has none yet — treating that as a husk would ingest
        the same document twice at once. The order below is cheapest-first.
        """
        # chunks.project_id is the project's ObjectId, not the notebook's uuid,
        # so the notebook id cannot be used to count them -- it matches nothing
        # and every document would look like a husk. One extra lookup, and only
        # on the duplicate path.
        project = await self.projects.get_project(project_id)
        indexed = await ChunkModel(self.db).count_project_chunks(project.id, asset_id)

        if indexed:
            return False

        if await self.tasks.find_active_for_project(project_id) is not None:
            return False

        async for task in self.tasks.iter_project_tasks(project_id):
            if task.asset_id == asset_id and task.status in (
                TaskExecutionStatus.FAILURE.value,
                TaskExecutionStatus.DEAD.value,
            ):
                return True

        return False

    async def queue(self, project_id: str, asset: Asset, args: dict) -> QueuedIngestion:
        """Publish the ingestion chain for *asset* and record a row per link.

        Rolls the asset back if the broker refuses. Left behind it would be
        worse than the outage itself: nothing would ever ingest it, and the
        content-hash dedupe in :meth:`store` would answer 409 to every retry
        of the same file, so the document could never be added at all without
        deleting it by hand first.

        Imported here rather than at module scope, and this is not stylistic:
        ``tasks.workflows`` reaches ``tasks.jobs.index``, which imports
        ``application.services`` — so a module-level import would have the
        services package waiting on a module that is waiting on the services
        package.
        """
        from application.tasks.tracking.status import mark_queued
        from application.tasks.workflows import (
            QueuedChain,
            chain_task_names,
            ingestion_signature,
        )

        # Ids first, then publish. Ingestion is no longer a chain, so there is
        # no .parent to walk for them afterwards -- and the collector needs
        # the index ids minutes later, long after this request is gone.
        queued = QueuedChain()

        try:
            ingestion_signature(project_id, args, queued).apply_async()

        except CELERY_BROKER_EXCEPTIONS as exc:
            # delete_asset only, matching the delete-source route: nothing in
            # this codebase unlinks an id from project.assets_ids, and adding a
            # one-off here would be the only place that does.
            await self.assets.delete_asset(asset.asset_id)

            raise CeleryBrokerError(f"Could not queue ingestion for {asset.name!r}") from exc

        # One row per stage, written before any of them runs. The planner's
        # id is what the browser polls; the other two are filled in by the
        # collector when it publishes them.
        idempotency = IdempotencyService(self.db)

        for name, task_id in zip(chain_task_names(), queued.ids):
            mark_queued(task_id)
            await idempotency.record(
                task_id=task_id,
                task_name=name,
                project_id=project_id,
                args=args,
                asset_id=asset.asset_id,
            )

        self.logger.info("Queued %r for project %r as task %r", asset.name, project_id, queued.process_id)

        return QueuedIngestion(task_id=queued.process_id, index_task_id=queued.index_id)
