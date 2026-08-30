"""Every key `.env.example` shows must be a real Settings field.

This is the regression test for a real outage: OLLAMA_BASE_URL (and, in the
same rename, MONGO_URI / VECTOR_DB_URL) stopped being Settings fields when the
config moved to HOST/PORT pairs, but `.env.example` — and a deployed
`Docker/env/.env.app` copied from an older version of it — kept assigning the
old names. `extra="ignore"` means pydantic-settings drops an unknown key
silently rather than raising, so the field it *should* have set quietly kept
its default (`OLLAMA_HOST="localhost"`) instead. Inside a container that
default is the container itself, not the host running Ollama, and the app
started up looking healthy while every model call failed.

A wrong value fails loudly — Settings already covers that, one enum member at
a time. A *renamed* value fails silently, and only this kind of test catches
it: nothing about constructing Settings normally can tell a key that was
dropped as unknown from one that was never set at all.
"""

import re
from pathlib import Path

from utils.config import SRC_DIR, Settings

ENV_EXAMPLE = SRC_DIR / ".env.example"

# A key, active or shown commented-out as an optional example — either way,
# it is documenting a name someone will type into their own .env, so either
# way it has to still be a real field.
_KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)\s*=")


def documented_keys(path: Path) -> set[str]:
    keys = set()

    for line in path.read_text().splitlines():
        match = _KEY.match(line.strip())
        if match:
            keys.add(match.group(1))

    return keys


def test_the_example_file_exists():
    """Guards every test below against silently checking nothing."""
    assert ENV_EXAMPLE.is_file()


def test_every_documented_key_is_a_real_field():
    keys = documented_keys(ENV_EXAMPLE)

    # Sanity floor: a parser that regressed to matching nothing would
    # otherwise make every real assertion below vacuously true.
    assert len(keys) > 20

    unknown = keys - set(Settings.model_fields)
    assert not unknown, (
        f"{ENV_EXAMPLE.name} sets {sorted(unknown)}, which {Settings.__name__} "
        "no longer has a field for — a rename left behind a stale key that "
        "pydantic-settings' extra=\"ignore\" will drop without a word."
    )


def test_the_ollama_host_and_port_are_still_there_specifically():
    """The exact fields the outage traced back to.

    Doesn't just confirm they parse as *some* field — confirms they are still
    named what ProviderCache and the two provider factories actually read
    (`ollama_base_url`, built from these two), so a future rename has to
    touch this file or fail here.
    """
    assert {"OLLAMA_HOST", "OLLAMA_PORT"} <= documented_keys(ENV_EXAMPLE)
    assert "OLLAMA_HOST" in Settings.model_fields
    assert "OLLAMA_PORT" in Settings.model_fields


def test_ollama_base_url_is_not_a_field(settings):
    """The name that broke. If this ever starts passing by *adding* the field
    back rather than by fixing a stale reference, that's worth knowing too."""
    assert "OLLAMA_BASE_URL" not in Settings.model_fields
    assert not hasattr(settings, "OLLAMA_BASE_URL")
