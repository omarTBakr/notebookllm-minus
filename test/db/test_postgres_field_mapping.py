"""The Postgres repositories must write fields their models actually have.

These are static checks rather than round-trips: they need no server, and the
failures they catch are exactly the ones a running server turns into a 503
("'User' object has no attribute 'name'") on the very first request.
"""

import re
from pathlib import Path

import pytest

from models.db_schema import Asset, Chat, DataChunk, Message, Project, Session, User

REPOSITORIES = [
    ("user", User),
    ("session", Session),
    ("chat", Chat),
    ("message", Message),
    ("project", Project),
    ("asset", Asset),
    ("chunk", DataChunk),
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
