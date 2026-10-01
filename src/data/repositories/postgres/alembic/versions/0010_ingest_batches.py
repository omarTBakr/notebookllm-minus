"""parsed page batches, held while an ingestion is in flight

A document is ingested in batches: each is parsed, corrected by a model, and
only when *every* batch of an asset is back can the pages be put in order, cut
into chunks and stored. Something has to hold the parsed pages in the meantime
and say when the set is complete.

That was a Celery chord, and it did not work. The chord's accumulator recorded
one member of twenty-three -- `replace()` with a chord whose header is a group
of chains marks only one tail as a chord member on Celery 5.6.3 -- so the
callback never fired, and a document whose every batch had succeeded produced no
chunks at all, silently, with its results still sitting in the result backend.

These two tables replace that accounting with rows.

`ingest_batches` is both the store and the counter. One row per
(asset_id, batch_index), so "how many are done" is a `count(*)` that cannot
drift, cannot exceed the total, and is unaffected by a task being redelivered --
which the previous Redis INCR counter was not, and which is how a progress bar
reached 104%.

The payload is JSONB and it is big: a batch of ten pages carries every word and
every bounding box, which is what a citation highlight is later drawn from. It
lives here rather than travelling through the broker or the result backend
precisely because it is big -- RabbitMQ is not a file server, and the Redis
backend is capped with an LRU eviction policy that would drop it.

`ingest_runs` is one row per asset in flight, and exists for `collected_at`: the
claim that decides which of the concurrently finishing batches gets to run the
collector. An `UPDATE ... WHERE collected_at IS NULL ... RETURNING` has exactly
one winner however many workers finish at the same instant. It also carries the
downstream task ids, generated when the upload was accepted so the rows the
browser polls exist before the work does.

Both tables are scratch: the collector deletes an asset's rows once its chunks
are stored. A row still present is an ingestion that did not finish.

Revision ID: 0010_ingest_batches
Revises: 0009_artifacts
Create Date: 2026-09-11

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_ingest_batches"
down_revision: Union[str, Sequence[str], None] = "0009_artifacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # if_not_exists throughout, as every migration here does since the
    # advisory-lock startup path was added: a re-run must be a no-op rather
    # than a DuplicateTable crash while the app is booting.
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.String(length=24), nullable=False),
        sa.Column("asset_id", sa.String(length=200), nullable=False),
        sa.Column("project_id", sa.String(length=200), nullable=False),
        sa.Column("total_batches", sa.Integer(), nullable=False),
        # The arguments the planner was given -- chunk_size, overlap_size,
        # reset. The collector runs in a different process minutes later and
        # needs them to chunk with; passing them through every batch message
        # instead would repeat them N times for no gain.
        sa.Column(
            "request_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # The project row's ObjectId, as a string. DataChunk.project_id is
        # typed as an ObjectId and this is not it -- it is the same value
        # spelled for JSON, rebuilt by the collector.
        sa.Column("project_object_id", sa.String(length=24), nullable=False, server_default=""),
        # Generated when the upload was accepted, so the task_executions rows
        # the browser polls exist before these tasks are published.
        sa.Column("index_task_id", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("build_task_id", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("parent_task_id", sa.String(length=200), nullable=False, server_default=""),
        # NULL until a batch claims the collection. The claim is what replaces
        # the chord: exactly one UPDATE can move it off NULL.
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id"),
        if_not_exists=True,
    )

    op.create_table(
        "ingest_batches",
        sa.Column("id", sa.String(length=24), nullable=False),
        sa.Column("asset_id", sa.String(length=200), nullable=False),
        sa.Column("project_id", sa.String(length=200), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        # parsed -> corrected. The collector fires when every row of an asset
        # reads `corrected`.
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("asset_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )

    # Unique, not merely indexed. It is what makes the row count an honest
    # denominator: a redelivered parse task upserts its own row rather than
    # adding a second one, so "corrected == total" cannot be reached early and
    # progress cannot pass 100%.
    op.create_index(
        "uq_ingest_batches_asset_index",
        "ingest_batches",
        ["asset_id", "batch_index"],
        unique=True,
        if_not_exists=True,
    )

    # The collector's read and the completion count are both "this asset's rows".
    op.create_index(
        "idx_ingest_batches_asset_status",
        "ingest_batches",
        ["asset_id", "status"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_ingest_batches_asset_status", table_name="ingest_batches", if_exists=True)
    op.drop_index("uq_ingest_batches_asset_index", table_name="ingest_batches", if_exists=True)
    op.drop_table("ingest_batches", if_exists=True)
    op.drop_table("ingest_runs", if_exists=True)
