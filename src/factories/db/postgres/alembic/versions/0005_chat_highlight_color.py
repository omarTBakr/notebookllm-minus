"""give chats a highlight color for cited passages

Every other per-chat setting (temperature, chunk_size, ...) is nullable and
falls back to a .env-wide default at read time — there is nowhere for a
highlight color to fall back to, so this one is NOT NULL with a real default
instead, matching web_search rather than temperature.

Revision ID: 0005_chat_highlight_color
Revises: 0004_chunk_lookup_index
Create Date: 2026-08-27

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0005_chat_highlight_color'
down_revision: Union[str, Sequence[str], None] = '0004_chunk_lookup_index'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "ALTER TABLE chats "
        "ADD COLUMN IF NOT EXISTS highlight_color VARCHAR(7) "
        "NOT NULL DEFAULT '#FFFF00'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS highlight_color")
