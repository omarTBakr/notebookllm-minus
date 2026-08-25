"""give assets a content hash, looked up per project

Uploading the same file twice created two rows: asset_id is a fresh uuid every
time and nothing else was compared, so a notebook could hold the same document
any number of times, each with its own chunks and its own vectors.

sha256 of file_bytes, backfilled for rows that predate this, and looked up per
project — the same file in a *different* notebook is a different document and
stays allowed.

The index here is only for the lookup. Uniqueness is 0004, kept separate
because an existing database may already hold duplicates — this one did — and
choosing which copy to delete is the user's call, not a migration's.

The route is the mechanism either way: it looks the hash up before writing
anything, so a duplicate is refused before it is chunked and embedded.

Revision ID: 0003_asset_content_hash
Revises: 0002_reconcile_sessions
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003_asset_content_hash'
down_revision: Union[str, Sequence[str], None] = '0002_reconcile_sessions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE assets "
        "ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64) NOT NULL DEFAULT ''"
    )

    # Backfill before the unique index goes on: every existing row would
    # otherwise carry '' and any two in one project would collide.
    op.execute(
        "UPDATE assets SET content_hash = encode(sha256(file_bytes), 'hex') "
        "WHERE content_hash = ''"
    )

    # A plain index, not a unique one. The uniqueness backstop is migration
    # 0004, and it is separate on purpose: a database that already holds
    # duplicates cannot build a unique index, and a migration is the wrong
    # place to decide which of a user's documents to delete. The route rejects
    # duplicates from here on regardless; 0004 goes on once what is already
    # stored is clean.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_assets_project_content "
        "ON assets (project_id, content_hash)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_assets_project_content")
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS content_hash")
