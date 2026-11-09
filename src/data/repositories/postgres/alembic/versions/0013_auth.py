"""accounts: email + password on users, and signed-in browser sessions

Users had no credentials: a profile was an opaque id picked from a list. They
now sign up with an email and a password (argon2 hash). Both columns are
nullable: every profile created before this has neither until it is claimed
at signup. auth_sessions holds one row per signed-in browser, keyed by the
sha256 of its cookie token.

Revision ID: 0013_auth
Revises: 0012_asset_source_url
Create Date: 2026-11-09

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_auth"
down_revision: Union[str, Sequence[str], None] = "0012_asset_source_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(320)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(512)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email) WHERE email IS NOT NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id VARCHAR(24) PRIMARY KEY,
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            user_id VARCHAR(200) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions (expires_at)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS auth_sessions")
    op.execute("DROP INDEX IF EXISTS uq_users_email")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_hash")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email")
