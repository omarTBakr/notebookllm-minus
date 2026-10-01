from abc import ABC, abstractmethod
from typing import AsyncIterator

from data.models import IngestBatch, IngestRun


class IngestBatchRepository(ABC):
    """Parsed pages held between the stages of one ingestion.

    This is what replaced a Celery chord, so the invariants below are not
    incidental — each one is the thing the chord failed to provide.
    """

    @abstractmethod
    async def start_run(self, run: IngestRun) -> None:
        """Record an asset's ingestion before any batch is published.

        An upsert on asset_id: re-ingesting a document that failed halfway must
        reset the run rather than collide with the row left behind.
        """

    @abstractmethod
    async def find_run(self, asset_id: str) -> IngestRun | None:
        """One asset's run, or None if it was never started or already cleared."""

    @abstractmethod
    async def save_batch(self, batch: IngestBatch) -> None:
        """Write a batch, replacing any earlier attempt at the same index.

        Upsert on (asset_id, batch_index), and that is what keeps the count
        honest: a redelivered task must overwrite its own row, never add a
        second one, or `count_corrected` could reach the total early and the
        collector would run on a half-parsed document.
        """

    @abstractmethod
    async def find_batch(self, asset_id: str, batch_index: int) -> IngestBatch | None:
        """One batch, or None."""

    @abstractmethod
    async def count_corrected(self, asset_id: str) -> int:
        """How many of an asset's batches have been through the model."""

    @abstractmethod
    async def claim_collection(self, asset_id: str) -> bool:
        """Claim the right to collect this asset. True for exactly one caller.

        The single-winner claim that replaces the chord's callback. Batches
        finish concurrently on however many workers consume the queue, and each
        one asks; the claim must be atomic, so that the last to finish starts
        the collector and the others simply stop.

        Must only succeed once every batch is corrected — a claim granted early
        would chunk a document that is still being parsed.
        """

    @abstractmethod
    async def iter_batches(self, asset_id: str) -> AsyncIterator[IngestBatch]:
        """Every batch of one asset, in batch_index order.

        Ordered here rather than by the caller so the collector cannot
        accidentally assemble a document out of sequence — every page would be
        present and each one readable, which is why that bug survives.
        """

    @abstractmethod
    async def clear_run(self, asset_id: str) -> None:
        """Delete an asset's batches and its run. Called once it is chunked.

        These rows hold the full text of a document; leaving them would keep a
        second copy of every ingested book forever.
        """
