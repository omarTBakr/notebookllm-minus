"""reconcile sessions with the model

0001 is written with if_not_exists on every create, so that it can run against
a database whose tables predate Alembic. That makes it idempotent, but it also
means an *existing* table is left exactly as it was — including where 0001
would have corrected it.

`sessions.title` is the case that bites: it is non-Optional on the Session
model and 0001 creates it, but a database carried over from before Alembic has
the table without it, and every read of a user's sessions then fails with
UndefinedColumnError — which takes notebook creation down with it.

Additive and idempotent, so it is safe on a database that already has the
column (one created by 0001 on a clean server) as well as one that does not.

Revision ID: 0002_reconcile_sessions
Revises: 0001_initial_schema
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002_reconcile_sessions'
down_revision: Union[str, Sequence[str], None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # IF NOT EXISTS rather than a reflection check: this has to be a no-op on a
    # database 0001 built correctly, and Postgres will do that test for us.
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL DEFAULT 'New session'"
    )

    # The timestamps on a pre-Alembic sessions table are nullable; the model
    # types them as required, so a NULL row fails validation on read.
    op.execute("UPDATE sessions SET created_at = now() WHERE created_at IS NULL")
    op.execute("UPDATE sessions SET updated_at = now() WHERE updated_at IS NULL")
    op.execute("ALTER TABLE sessions ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE sessions ALTER COLUMN updated_at SET DEFAULT now()")
    op.execute("ALTER TABLE sessions ALTER COLUMN created_at SET NOT NULL")
    op.execute("ALTER TABLE sessions ALTER COLUMN updated_at SET NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE sessions ALTER COLUMN created_at DROP NOT NULL")
    op.execute("ALTER TABLE sessions ALTER COLUMN updated_at DROP NOT NULL")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS title")
