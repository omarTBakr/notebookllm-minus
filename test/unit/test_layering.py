"""The three tiers only depend downward.

    presentation -> application -> data -> shared

`shared` depends on nothing in the project; `data` on `shared`; `application`
on `data` and `shared`; `presentation` on `application` and `shared`. A route
reaching for a repository, or a service importing the web framework, is how a
tier quietly stops being one, so the rule is checked on every import.

Composition roots (`main.py`, `celery_app.py`, ...) sit above all of it and are
not checked.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

TIERS = ("presentation", "application", "data", "shared")

ALLOWED = {
    "shared": set(),
    "data": {"shared"},
    "application": {"data", "shared"},
    "presentation": {"application", "shared"},
}

# Web-framework types have no business below the routes.
NO_WEB_FRAMEWORK = ("application", "data", "shared")
WEB_FRAMEWORK = {"fastapi", "starlette"}

# The routes talk to services, never to a repository or a stored model.
ROUTES_MAY_NOT_IMPORT = {"data"}


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _files(tier: str):
    for path in (SRC / tier).rglob("*.py"):
        if "benchmark" in path.parts or "alembic" in path.parts:
            continue
        yield path


def _cases(tiers):
    return [(tier, path) for tier in tiers for path in _files(tier)]


@pytest.mark.parametrize(("tier", "path"), _cases(TIERS), ids=lambda v: str(v.relative_to(SRC)) if isinstance(v, Path) else v)
def test_a_tier_only_imports_downward(tier, path):
    offending = sorted(
        module
        for module in _imports(path)
        if module.split(".")[0] in TIERS and module.split(".")[0] != tier and module.split(".")[0] not in ALLOWED[tier]
    )
    assert not offending, f"{path.relative_to(SRC)} ({tier}) imports upward: {offending}"


@pytest.mark.parametrize(("tier", "path"), _cases(NO_WEB_FRAMEWORK), ids=lambda v: str(v.relative_to(SRC)) if isinstance(v, Path) else v)
def test_only_presentation_knows_the_web_framework(tier, path):
    offending = sorted(m for m in _imports(path) if m.split(".")[0] in WEB_FRAMEWORK)
    assert not offending, f"{path.relative_to(SRC)} imports {offending}"


@pytest.mark.parametrize("path", [p for p in _files("presentation") if "web" not in p.parts], ids=lambda p: str(p.relative_to(SRC)))
def test_routes_do_not_touch_the_data_tier(path):
    offending = sorted(m for m in _imports(path) if m.split(".")[0] in ROUTES_MAY_NOT_IMPORT)
    assert not offending, f"{path.relative_to(SRC)} imports {offending}; go through a service"


# The routes get their services from presentation/dependencies.py, which is the
# one place that may read the database and the provider cache off the app.
APP_WIRING = ("app.db", "app.providers", "app.embedding_client", "app.generation_client")


@pytest.mark.parametrize(
    "path",
    [p for p in _files("presentation") if p.name != "dependencies.py"],
    ids=lambda p: str(p.relative_to(SRC)),
)
def test_only_dependencies_reads_the_apps_wiring(path):
    source = path.read_text()
    offending = [needle for needle in APP_WIRING if needle in source]
    assert not offending, f"{path.relative_to(SRC)} reads {offending}; build the service in presentation/dependencies.py"
