"""Alembic environment for the Postgres backend.

Two entry points share this file:

* the CLI (`alembic -c factories/db/postgres/alembic.ini upgrade head`), which
  has to build its own engine, and
* ``PostgresProvider.setup_indexes()``, which already holds a connection from
  the running app and hands it over in ``config.attributes["connection"]``.

The DSN never appears in alembic.ini. It comes from the same ``Settings``
object the app uses, so there is one source of truth for POSTGRES_* and no
password in a committed file.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# alembic.ini sets prepend_sys_path, but only the CLI reads it — and only
# relative to how it was invoked. Anchoring on this file makes `src` importable
# no matter who started us. factories/db/postgres/alembic/env.py -> src/
SRC_DIR = Path(__file__).resolve().parents[4]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from factories.db.postgres.base_repository import Base  # noqa: E402
from utils.config import get_settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # When the app calls us in-process it has already configured logging, and
    # fileConfig would tear that down and replace it.
    if config.attributes.get("connection") is None:
        fileConfig(config.config_file_name)

# What `--autogenerate` diffs the live database against.
target_metadata = Base.metadata


def include_name(name, type_, parent_names) -> bool:
    """Keep runtime-created tables out of the autogenerate diff.

    PostgresVectorRepository creates one `vec_project_<uuid>` table per project
    at runtime, deliberately outside the metadata — their number and names are
    not known until a notebook exists. Autogenerate has no way to know that and
    reported each one as a table to DROP, so every run produced a migration
    that would have deleted every vector in the database.

    Returning False here excludes them from the comparison entirely, which is
    what makes "autogenerate against a database at head is empty" a check worth
    running rather than noise to be read past.
    """
    if type_ == "table" and name is not None and name.startswith("vec_project_"):
        return False

    return True


def _url() -> str:
    return get_settings().postgres_async_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it. Needs no database."""
    context.configure(
        url=get_settings().postgres_url,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=include_name,
        # Without this, a column whose type changes reads as "no change".
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """CLI path: build an engine of our own and migrate over it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}) | {"sqlalchemy.url": _url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")

    if connection is not None:
        # In-process: the app is already inside an async transaction on this
        # connection and holds the advisory lock. Reuse it — opening a second
        # connection here would deadlock against that lock.
        do_run_migrations(connection)
        return

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
