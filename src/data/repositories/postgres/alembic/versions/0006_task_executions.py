"""record every celery task run, so task history outlives Redis

Celery's own state lives in the result backend for CELERY_RESULT_EXPIRES
seconds and then vanishes. That is enough for "is it done yet?" and nothing
else: it cannot be joined to the project the work was for, a finished task
becomes indistinguishable from one that never existed once the key expires,
and a task whose worker died stays STARTED forever because nothing outlives
the worker to say otherwise.

Two of the three indexes here are the point of the table rather than tuning.
idx_tasks_project_created is what the browser's progress poll reads, replacing
a process-local dict that answered wrongly whenever the poll reached a
different API worker than the upload. idx_tasks_name_hash_status backs the
idempotency check, and is deliberately NOT unique: submitting the same work
again after the first run has finished is legitimate, and a unique constraint
would turn a harmless concurrent double-submit into a 500 instead of letting
the second caller join the first.

Revision ID: 0006_task_executions
Revises: 0005_chat_highlight_color
Create Date: 2026-09-04

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_task_executions"
down_revision: Union[str, Sequence[str], None] = "0005_chat_highlight_color"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # IF NOT EXISTS throughout, as everywhere in this project's migrations:
    # startup runs them under an advisory lock against a database that may
    # already have been created by an earlier metadata.create_all.
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_executions (
            id            VARCHAR(24)  PRIMARY KEY,
            task_id       VARCHAR(200) NOT NULL UNIQUE,
            task_name     VARCHAR(200) NOT NULL,
            project_id    VARCHAR(200) NOT NULL,
            asset_id      VARCHAR(200) NOT NULL DEFAULT '',
            status        VARCHAR(20)  NOT NULL,
            stage         VARCHAR(50)  NOT NULL DEFAULT '',
            done          INTEGER      NOT NULL DEFAULT 0,
            total         INTEGER      NOT NULL DEFAULT 0,
            args          JSONB        NOT NULL DEFAULT '{}'::jsonb,
            args_hash     VARCHAR(64)  NOT NULL DEFAULT '',
            result        JSONB        NOT NULL DEFAULT '{}'::jsonb,
            error         TEXT         NOT NULL DEFAULT '',
            error_type    VARCHAR(200) NOT NULL DEFAULT '',
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_created " "ON task_executions (project_id, created_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_name_hash_status " "ON task_executions (task_name, args_hash, status)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_tasks_name_hash_status")
    op.execute("DROP INDEX IF EXISTS idx_tasks_project_created")
    op.execute("DROP TABLE IF EXISTS task_executions")
