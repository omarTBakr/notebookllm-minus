"""one copy of a document per notebook, enforced

The index this creates has been described in the codebase since 0003 and
deferred three times. `postgres/asset_repository.py` already says the dedupe
lookup is "served by uq_assets_project_content"; Mongo has enforced the same
rule from the start (`mongo/provider.py`, a partial unique index). Only
Postgres let a project hold the same bytes twice.

It was deferred because the databases held duplicates, and choosing which copy
to keep is not a migration's decision to make. That is why this does not
deduplicate anything: it checks, and stops with a message naming the rows if
any remain. Silently deleting one of two assets a user uploaded would be a far
worse outcome than a failed upgrade.

Partial, matching Mongo's `$gt: ""`: content_hash defaults to '' and every row
written before 0003 carries that sentinel, so an unconditional unique index
would make all of them collide with each other rather than with a real
duplicate.

Also drops idx_assets_project_content from 0003. It covered the same two
columns in the same order, so the unique index replaces it exactly — keeping
both would mean maintaining two identical btrees on every asset write.

Revision ID: 0007_unique_asset_content
Revises: 0006_task_executions
Create Date: 2026-09-04

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_unique_asset_content"
down_revision: Union[str, Sequence[str], None] = "0006_task_executions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Refuse to continue rather than let CREATE UNIQUE INDEX fail with
    # "could not create unique index ... Key (project_id, content_hash)=(...)",
    # which names one colliding pair and nothing about how to resolve it.
    # Migrations run under an advisory lock during startup, so the operator
    # sees this in the application's own logs and needs it to be actionable.
    op.execute("""
        DO $$
        DECLARE
            offenders text;
        BEGIN
            SELECT string_agg(
                       format('project %s has %s copies of %s', project_id, n, names),
                       E'\\n'
                   )
              INTO offenders
              FROM (
                    SELECT project_id,
                           count(*)                AS n,
                           string_agg(name, ', ')  AS names
                      FROM assets
                     WHERE content_hash <> ''
                     GROUP BY project_id, content_hash
                    HAVING count(*) > 1
                   ) AS duplicates;

            IF offenders IS NOT NULL THEN
                RAISE EXCEPTION
                    'Cannot enforce one-copy-per-notebook: duplicate assets exist.%s%s%s%s',
                    E'\\n', offenders, E'\\n',
                    'Delete the unwanted copy of each through the application, then retry.';
            END IF;
        END $$;
        """)

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_project_content "
        "ON assets (project_id, content_hash) "
        "WHERE content_hash <> ''"
    )

    # Redundant once the line above exists: same columns, same order.
    op.execute("DROP INDEX IF EXISTS idx_assets_project_content")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("CREATE INDEX IF NOT EXISTS idx_assets_project_content " "ON assets (project_id, content_hash)")
    op.execute("DROP INDEX IF EXISTS uq_assets_project_content")
