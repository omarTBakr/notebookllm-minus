"""a one-line precis per chunk, for generating study material

The Studio features — flashcards, quizzes — are about a whole notebook, not
about one question, so top-k retrieval is the wrong tool: five chunks cannot
describe a book. But the whole book does not fit either. The 222-page Arabic
guide is 694 chunks and ~470k characters, an order of magnitude past any
context window in use here.

694 one-line summaries is roughly 35k tokens, which does fit. So the model is
given summaries and never the raw text, while the chunk stays behind as the
citation target — a card can be traced to a page the model never read verbatim.

Not nullable, defaulting to the empty string, deliberately. "" means "not
summarised yet", which makes the generation task resumable: it skips any chunk
that already has one, so a worker restart costs nothing but the batch in
flight. A nullable column would say the same thing in a three-valued logic that
every query would then have to handle.

Backfilled with '' rather than left NULL for the same reason — existing chunks
are simply not summarised yet, and the first Studio use will fill them in.

Revision ID: 0008_chunk_summary
Revises: 0007_unique_asset_content
Create Date: 2026-09-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_chunk_summary"
down_revision: Union[str, Sequence[str], None] = "0007_unique_asset_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # if_not_exists so a re-run is a no-op rather than a DuplicateColumn crash
    # at startup — the same rule every migration here follows since the
    # advisory-lock startup path was added.
    op.add_column(
        "chunks",
        sa.Column(
            "summary",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # The summaries are derived data: dropping them costs model calls to
    # regenerate, not information.
    op.drop_column("chunks", "summary", if_exists=True)
