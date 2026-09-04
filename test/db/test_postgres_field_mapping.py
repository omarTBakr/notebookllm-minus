"""The Postgres schema, the repositories and the models must agree.

Three ways they can drift, and a check for each — all static, so none of them
needs a server, and all of them fail at the point the mistake is made rather
than as a 503 on the first request:

1. a repository reads a field off a model that does not have it
   ("'Chat' object has no attribute 'name'"),
2. a model has a field with no column behind it, so writes vanish and reads
   hand back the default (this is what happened to Session.title), and
3. a column is nullable where the model is not, so a row written without it
   fails pydantic validation on the way back out.
"""

import re
from pathlib import Path
from types import NoneType
from typing import get_args

import pytest

from factories.db.postgres.base_repository import (
    AssetRow,
    ChatRow,
    ChunkRow,
    MessageRow,
    ProjectRow,
    SessionRow,
    TaskExecutionRow,
    UserRow,
)
from models.db_schema import (
    Asset,
    Chat,
    DataChunk,
    Message,
    Project,
    Session,
    TaskExecution,
    User,
)

REPOSITORIES = [
    ("user", User),
    ("session", Session),
    ("chat", Chat),
    ("message", Message),
    ("project", Project),
    ("asset", Asset),
    ("chunk", DataChunk),
    ("task", TaskExecution),
]

# The pydantic model and the ORM class that stores it.
TABLES = [
    (User, UserRow),
    (Session, SessionRow),
    (Chat, ChatRow),
    (Message, MessageRow),
    (Project, ProjectRow),
    (Asset, AssetRow),
    (DataChunk, ChunkRow),
    (TaskExecution, TaskExecutionRow),
]

SRC = Path(__file__).resolve().parents[2] / "src" / "factories" / "db" / "postgres"

# Empty now that the three stale repositories were brought back in line with
# their models. Kept as the hook for the next one that drifts.
KNOWN_BROKEN: dict[str, str] = {}


def attributes_read_off_the_model(stem: str) -> set[str]:
    source = (SRC / f"{stem}_repository.py").read_text()
    return set(re.findall(rf"\b{stem}\.([a-z_]+)\b", source))


@pytest.mark.parametrize("stem, model", REPOSITORIES)
def test_every_referenced_field_exists_on_the_model(stem, model, request):
    if stem in KNOWN_BROKEN:
        request.node.add_marker(
            pytest.mark.xfail(strict=True, reason=f"KNOWN BUG: {KNOWN_BROKEN[stem]}")
        )

    path = SRC / f"{stem}_repository.py"
    if not path.exists():
        pytest.skip(f"no postgres {stem} repository")

    unknown = attributes_read_off_the_model(stem) - set(model.model_fields)
    unknown = {u for u in unknown if not u.startswith("_")}

    assert not unknown, (
        f"{path.name} reads {sorted(unknown)} off a {model.__name__}, which has "
        f"{sorted(model.model_fields)}"
    )


@pytest.mark.parametrize("stem, model", REPOSITORIES)
def test_the_repository_names_its_model_argument_after_the_model(stem, model):
    """The check above greps for `<stem>.<attr>`, so it only sees anything if
    the repository actually calls its argument `user`, `chat`, and so on. A
    rename would turn it into a test that passes by finding nothing."""
    if stem in ("chunk", "message"):
        pytest.skip("create_chunks/create_message iterate; covered by the parity tests")

    source = (SRC / f"{stem}_repository.py").read_text()

    assert re.search(rf"\b{stem}\.[a-z_]+", source), (
        f"{stem}_repository.py never reads an attribute off a variable named "
        f"{stem!r}, so test_every_referenced_field_exists_on_the_model is vacuous"
    )


@pytest.mark.parametrize("model, row", TABLES, ids=lambda p: getattr(p, "__name__", p))
def test_every_model_field_has_a_column(model, row):
    """A field with no column behind it is written to nothing and read back as
    its default — silently. Session.title was exactly this for months."""
    columns = set(row.__table__.columns.keys())

    # The models alias the primary key to `_id`; the column is plain `id`.
    missing = set(model.model_fields) - columns

    assert not missing, (
        f"{model.__name__} has {sorted(missing)} with no column on "
        f"{row.__tablename__}, which has {sorted(columns)}"
    )


def _tolerates_none(annotation) -> bool:
    return NoneType in get_args(annotation)


@pytest.mark.parametrize("model, row", TABLES, ids=lambda p: getattr(p, "__name__", p))
def test_a_required_field_is_not_backed_by_a_nullable_column(model, row):
    """pydantic refuses None for a field typed `str` or `list[str]`, however
    generous its default_factory is. So a nullable column behind one of those
    means any row written without it fails validation on the way back out."""
    offenders = sorted(
        name
        for name, field in model.model_fields.items()
        if not _tolerates_none(field.annotation)
        and name in row.__table__.columns
        and row.__table__.columns[name].nullable
    )

    assert not offenders, (
        f"{row.__tablename__} makes {offenders} nullable, but {model.__name__} "
        f"does not accept None for them"
    )
