"""generated study material, kept per notebook

Flashcard decks and quizzes produced from a notebook's documents. They are
derived data — regenerable from the chunks and their summaries — but expensive
enough to be worth keeping: a deck over a 694-chunk book costs on the order of
seventy model calls, and asking for it again on every page load would be absurd.

One row per (notebook, kind), enforced by a unique index. A notebook does not
accumulate a history of decks: the useful question is "what is the current
flashcard set for this book", and keeping every past attempt would make the UI
ask which one the user meant. Regenerating replaces.

`items` is a JSONB *array*, not an object, because it is appended to. The
generating task adds a batch at a time while the browser may already be
displaying the set, so the append has to happen in the database — a
read-modify-write from the worker would lose items to any concurrent write and
would briefly serve a shorter deck than the one already on screen.

`status` distinguishes a set that is still filling from one that is finished,
which is what lets a partial deck be shown honestly rather than looking
complete. A failed run keeps whatever it produced: twelve cards that stopped
early beat an error page.

Revision ID: 0009_artifacts
Revises: 0008_chunk_summary
Create Date: 2026-09-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_artifacts"
down_revision: Union[str, Sequence[str], None] = "0008_chunk_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # if_not_exists throughout, as every migration here does since the
    # advisory-lock startup path was added: a re-run must be a no-op rather
    # than a DuplicateTable crash while the app is booting.
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=24), nullable=False),
        sa.Column("artifact_id", sa.String(length=200), nullable=False),
        # The notebook's business id — the string a URL carries, matching
        # assets.project_id. Deliberately not the project row's ObjectId that
        # chunks.project_id holds; artifacts are reached from chat routes.
        sa.Column("chat_id", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("source_task_id", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
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
        sa.UniqueConstraint("artifact_id"),
        if_not_exists=True,
    )

    # One current set per notebook per kind. Unique rather than merely indexed:
    # two rows for the same pair would make "the deck for this book" ambiguous
    # with nothing to break the tie, and it is what the upsert in
    # create_artifact conflicts on.
    op.create_index(
        "uq_artifacts_chat_kind",
        "artifacts",
        ["chat_id", "kind"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_artifacts_chat_kind", table_name="artifacts", if_exists=True)
    op.drop_table("artifacts", if_exists=True)
