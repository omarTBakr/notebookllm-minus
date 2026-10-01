"""record the link a source was added from

A source can now come from a link as well as an upload: a PDF downloaded from
its URL, an article read from its page, a YouTube video's transcript. The link
is what the preview's "open original" points at and what the video player
plays, so it is stored with the asset. '' for every upload, which is every row
that predates this.

Revision ID: 0012_asset_source_url
Revises: 0011_personal_user_information
Create Date: 2026-09-21

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_asset_source_url"
down_revision: Union[str, Sequence[str], None] = "0011_personal_user_information"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE assets " "ADD COLUMN IF NOT EXISTS source_url VARCHAR(2048) NOT NULL DEFAULT ''")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS source_url")
