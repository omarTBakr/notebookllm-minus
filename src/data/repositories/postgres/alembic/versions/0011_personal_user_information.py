"""personal_user_information: durable facts extracted from `/memory` messages

One row per (user_id, key) -- a `/memory` message naming a key that already
exists for that user replaces the description in place rather than adding a
row, which is what lets the matching vector (see PostgresVectorRepository,
collection `user_memory_<user_id>`) be updated by id instead of duplicated.
See plan `i-want-to-add-linear-pebble` for the feature this backs.

Revision ID: 0011_personal_user_information
Revises: 0010_ingest_batches
Create Date: 2026-09-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_personal_user_information"
down_revision: Union[str, Sequence[str], None] = "0010_ingest_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # if_not_exists throughout, as every migration here does since the
    # advisory-lock startup path was added: a re-run must be a no-op rather
    # than a DuplicateTable crash while the app is booting.
    op.create_table(
        "personal_user_information",
        sa.Column("id", sa.String(length=24), nullable=False),
        sa.Column("user_id", sa.String(length=200), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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

    # Unique, not merely indexed: it is the ON CONFLICT target upsert_fact()
    # relies on to replace a key's value instead of adding a second row.
    op.create_index(
        "uq_personal_user_info_user_key",
        "personal_user_information",
        ["user_id", "key"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_personal_user_info_user_key", table_name="personal_user_information", if_exists=True)
    op.drop_table("personal_user_information", if_exists=True)
