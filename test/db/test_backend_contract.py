"""Both backends must implement the whole interface.

This is the point of having interfaces at all, and it was not true: the Mongo
repositories did not import the ABCs they claimed to implement, so seven
missing methods went unnoticed until the imports were repaired. An abstract
method left unimplemented is not a type error here, it is a TypeError the
moment the provider tries to build the repository — i.e. at first request.
"""

import inspect

import pytest

from factories.db import mongo, postgres

BACKENDS = {"mongo": mongo, "postgres": postgres}


def concrete_repositories(package):
    found = []
    for module in vars(package).values():
        if not inspect.ismodule(module):
            continue
        for name, obj in vars(module).items():
            if (inspect.isclass(obj)
                    and name.startswith(("Mongo", "Postgres", "Qdrant"))
                    and obj.__module__.startswith(package.__name__)):
                found.append(obj)
    return found


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_repository_is_instantiable(backend):
    package = BACKENDS[backend]
    repositories = concrete_repositories(package)

    assert repositories, f"no repositories discovered for {backend}"

    unimplemented = {
        cls.__name__: sorted(cls.__abstractmethods__)
        for cls in repositories
        if getattr(cls, "__abstractmethods__", None)
    }

    assert not unimplemented, (
        f"{backend} leaves interface methods unimplemented: {unimplemented}. "
        "Building the repository would raise TypeError at the first request."
    )


def own_methods(cls):
    """Methods declared in the class body, not inherited.

    Inherited ones are backend infrastructure — MongoBaseModel contributes
    create_index/get_index/patch_one, which Postgres has no equivalent of and
    no need for. Comparing those would be noise; comparing what each class
    actually declares is what catches a domain method that exists on one
    backend and not the other.
    """
    return {
        name for name, obj in vars(cls).items()
        if callable(obj) and not name.startswith("_")
    }


# Repository role -> the class name each backend gives it. The vector stores
# are excluded: Qdrant and pgvector back different engines and their surfaces
# legitimately differ.
PAIRS = [
    ("asset", "MongoAssetRepository", "PostgresAssetRepository"),
    ("chunk", "MongoChunkRepository", "PostgresChunkRepository"),
    ("chat", "MongoChatRepository", "PostgresChatRepository"),
    ("message", "MongoMessageRepository", "PostgresMessageRepository"),
    ("project", "MongoProjectRepository", "PostgresProjectRepository"),
    ("session", "MongoSessionRepository", "PostgresSessionRepository"),
    ("user", "MongoUserRepository", "PostgresUserRepository"),
    ("task", "MongoTaskRepository", "PostgresTaskRepository"),
]

# Mongo-only methods that nothing outside the Mongo package calls. Left in
# place rather than deleted, but named here so they cannot be mistaken for
# part of the shared surface — and so anything NEW that drifts still fails.
# Delete them from the Mongo repositories and this list shrinks to nothing.
UNUSED_MONGO_ONLY = {
    "asset": {"get_assets_by_project"},
    "chunk": {"get_project_chunks"},
    "project": {"clear_chunk_ids"},
}


def _find(package, name):
    for cls in concrete_repositories(package):
        if cls.__name__ == name:
            return cls
    raise AssertionError(f"{name} not found in {package.__name__}")


@pytest.mark.parametrize("role, mongo_name, postgres_name", PAIRS)
def test_both_backends_expose_the_same_methods(role, mongo_name, postgres_name):
    """A method on one backend and not the other is a 500 waiting for whoever
    switches DOCUMENT_DB_BACKEND.

    The ABC check above only catches methods somebody remembered to declare
    abstract, and two did not get declared. `iter_project_assets` and
    `remove_chunk_ids` existed only on Mongo, so `POST /process/{id}` raised
    AttributeError on Postgres — the default backend — for a bare call and for
    reset=true respectively, while every test passed against fakes.
    """
    only_mongo = own_methods(_find(mongo, mongo_name)) - own_methods(_find(postgres, postgres_name))
    only_mongo -= UNUSED_MONGO_ONLY.get(role, set())
    only_postgres = own_methods(_find(postgres, postgres_name)) - own_methods(_find(mongo, mongo_name))

    assert not (only_mongo or only_postgres), (
        f"{role} repositories have drifted — "
        f"only on mongo: {sorted(only_mongo)}; only on postgres: {sorted(only_postgres)}"
    )


# --- the unique index is a backstop, and must answer like the check it backs --


def test_a_unique_content_violation_reads_as_a_duplicate_not_a_db_fault():
    """uq_assets_project_content (migration 0007) only fires when two uploads
    of the same bytes race past the route's own dedupe check. That is still a
    duplicate, so it has to answer 409 like every other one — a generic DbError
    would report the collision as a 503 and blame the database for working."""
    import asyncio

    from sqlalchemy.exc import IntegrityError

    from exceptions import DuplicateAssetError
    from factories.db.postgres import PostgresAssetRepository
    from models.db_schema import Asset

    class ExplodingSession:
        def begin(self):
            raise IntegrityError(
                'duplicate key value violates unique constraint "uq_assets_project_content"',
                None,
                Exception("dup"),
            )

    repo = PostgresAssetRepository(ExplodingSession())

    asset = Asset(
        asset_id="a1",
        asset_type="text",
        project_id="p1",
        name="note.txt",
        content_hash="abc",
    )

    with pytest.raises(DuplicateAssetError, match="already in this notebook"):
        asyncio.run(repo.update_asset(asset))


def test_an_unrelated_integrity_error_is_still_a_database_fault():
    """Only the content-hash constraint means "duplicate". Anything else is a
    real fault and must not be disguised as a user-level 409."""
    import asyncio

    from sqlalchemy.exc import IntegrityError

    from exceptions import DbError
    from factories.db.postgres import PostgresAssetRepository
    from models.db_schema import Asset

    class ExplodingSession:
        def begin(self):
            raise IntegrityError("null value in column violates not-null", None, Exception("x"))

    repo = PostgresAssetRepository(ExplodingSession())

    asset = Asset(
        asset_id="a1",
        asset_type="text",
        project_id="p1",
        name="note.txt",
    )

    with pytest.raises(DbError):
        asyncio.run(repo.update_asset(asset))
