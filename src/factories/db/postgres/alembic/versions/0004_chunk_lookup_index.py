"""index chunks by (asset_id, chunk_order) so a citation can find its page

A search hit carries only what NLPController._flush put in the vector payload:
project_id, asset_id, chunk_order, source. The page number a citation needs
lives on the chunk row, in chunk_metadata, so turning a hit back into a place
in the document is a lookup by (asset_id, chunk_order) — the pair _point_key
already treats as a passage's stable identity.

Every existing chunk index leads with project_id, so that lookup had no index
to use and fell back to a scan. Answering one question means up to
RETRIEVAL_TOP_K of these lookups, on a table with a row per chunk of every
document in the installation.

asset_id alone would very nearly do — it is a uuid4 — but chunk_order is what
makes the lookup a range read rather than a filter over the whole document,
and a book runs to a couple of thousand chunks.

Note on numbering: 0003's docstring calls the assets (project_id,
content_hash) UNIQUE index "0004". It is still unwritten and still blocked on
the same thing — the database holds duplicates that only the user should
choose between — so it moves to 0005. This is 0004 because it is next.

Revision ID: 0004_chunk_lookup_index
Revises: 0003_asset_content_hash
Create Date: 2026-08-26

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004_chunk_lookup_index'
down_revision: Union[str, Sequence[str], None] = '0003_asset_content_hash'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # IF NOT EXISTS throughout this project's migrations: startup runs them
    # under an advisory lock against a database that may already have been
    # created by an earlier metadata.create_all.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_asset_order "
        "ON chunks (asset_id, chunk_order)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_chunks_asset_order")
