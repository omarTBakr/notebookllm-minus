"""Parsed pages, held between the stages of one ingestion.

A document is ingested in batches: each is parsed, corrected by a model, and
only when *every* batch of an asset is back can the pages be put in order, cut
into chunks and stored. Something has to hold the pages in the meantime and say
when the set is complete.

That was a Celery chord, and it did not work. Its accumulator recorded one
member of twenty-three, the callback never fired, and a document whose every
batch had succeeded produced no chunks at all -- silently, with the results
still sitting in the result backend. These two rows replace that accounting
with something a database can answer: a count, and a claim.

`IngestBatch` is both the store and the counter. One row per
(asset_id, batch_index) means "how many are done" is a `count(*)` that cannot
drift, cannot exceed the total, and does not care how many times a task was
redelivered.

`IngestRun` is one row per asset in flight, and exists for `collected_at`: the
single-winner claim that decides which of the concurrently finishing batches
runs the collector.

**Both are scratch.** The collector deletes an asset's rows once its chunks are
stored, and a row still present is an ingestion that did not finish. That
matters, because `payload` is the thing `summarize_result` refuses to keep --
the full text of a document, plus every word box on every page. Storing it is
acceptable here only because it is transient and because the alternatives are
worse: through the broker it would make RabbitMQ a file server, and in the
result backend it would meet a 512MB cap with an LRU eviction policy.
"""

from datetime import datetime
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from .project import utcnow


class IngestBatchStatus:
    """The two states a batch moves through.

    Plain strings rather than an enum in `enums/`: they are read only by the
    repository that writes them and never cross an API boundary.
    """

    PARSED = "parsed"
    CORRECTED = "corrected"


class IngestBatch(BaseModel):
    """One batch of parsed pages, before and after correction."""

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")

    asset_id: str = Field(..., min_length=1, max_length=200)
    project_id: str = Field(..., min_length=1, max_length=200)

    # Position in the document, and the other half of the uniqueness that keeps
    # the count honest. Not the page number: a batch spans up to
    # PDF_BATCH_PAGES of them.
    batch_index: int = Field(..., ge=0)

    status: str = Field(default=IngestBatchStatus.PARSED, max_length=20)

    asset_name: str = Field(default="", max_length=200)

    # The pages, as `PdfLayoutService.page_to_dict` serialises them: text,
    # word list, char offsets and bounding boxes, plus `corrected_text` and
    # `scale` once the model has been through it.
    payload: dict = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class IngestRun(BaseModel):
    """One asset's ingestion, from planning to collection."""

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")

    asset_id: str = Field(..., min_length=1, max_length=200)
    project_id: str = Field(..., min_length=1, max_length=200)

    # The denominator. Fixed when the document was opened and the page count
    # known, so progress has an honest total from the first poll.
    total_batches: int = Field(..., ge=0)

    # chunk_size / overlap_size / reset. The collector runs in another process
    # minutes later and chunks with them; repeating them in every batch message
    # would copy them N times for nothing.
    request_data: dict = Field(default_factory=dict)

    # The project row's ObjectId, as a string. DataChunk.project_id is typed as
    # an ObjectId and this is not one -- it is the same value spelled for JSON,
    # rebuilt by the collector.
    project_object_id: str = Field(default="", max_length=24)

    # Generated when the upload was accepted, so the task_executions rows the
    # browser polls exist before these tasks are ever published.
    parent_task_id: str = Field(default="", max_length=200)
    index_task_id: str = Field(default="", max_length=200)
    build_task_id: str = Field(default="", max_length=200)

    # None until a batch claims the collection. Exactly one writer can move it
    # off None, and that writer dispatches the collector.
    collected_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
