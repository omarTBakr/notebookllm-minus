"""Every file docker-compose.yml points at has to exist.

Compose does not validate a bind-mount source or a `dockerfile:` path until it
acts on them: a mount whose source is missing is silently created as an empty
*directory* on the host, and the service then starts against an empty config
(nginx with no proxy.conf, redis with no requirepass) instead of failing. That is
exactly what a directory move leaves behind, so it is checked here, statically.
"""

import re
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[2] / "Docker"
COMPOSE = DOCKER_DIR / "docker-compose.yml"
REPO_ROOT = DOCKER_DIR.parent

# services/ is gitignored and copied from services.example/ by hand, so a fresh
# clone has only the template; check that, and the real copy once it exists.
SERVICES = DOCKER_DIR / "services"
HAS_SERVICES = SERVICES.is_dir()


def _resolve(source: str) -> Path:
    if not HAS_SERVICES and source.startswith("./services/"):
        source = "./services.example/" + source.removeprefix("./services/")
    return DOCKER_DIR / source


def _compose_text() -> str:
    return COMPOSE.read_text()


def _mount_sources() -> list[str]:
    """`- ./services/x:/target[:ro]` entries. Named volumes (no ./) are skipped."""
    return re.findall(r"^\s*-\s*(\./[^\s:]+):", _compose_text(), re.MULTILINE)


def _env_files() -> list[str]:
    """`- ./env/.env.x` entries under an `env_file:` key."""
    return re.findall(r"^\s*-\s*(\./env/[^\s]+)\s*$", _compose_text(), re.MULTILINE)


def _dockerfiles() -> list[str]:
    return re.findall(r"^\s*dockerfile:\s*(\S+)", _compose_text(), re.MULTILINE)


def test_compose_file_exists():
    assert COMPOSE.is_file()


def test_the_parsers_find_something():
    """Guards every parametrised test below against checking an empty list."""
    assert len(_mount_sources()) >= 6
    assert len(_env_files()) >= 4
    assert len(_dockerfiles()) >= 1


@pytest.mark.parametrize("source", _mount_sources())
def test_every_bind_mount_source_exists(source):
    assert _resolve(source).exists(), f"compose mounts {source}, which does not exist"


@pytest.mark.parametrize("source", _mount_sources())
def test_every_mount_lives_under_services(source):
    assert source.startswith("./services/"), (
        f"{source}: a service's config belongs under Docker/services/<name>/"
    )


@pytest.mark.parametrize("path", _dockerfiles())
def test_every_dockerfile_exists(path):
    """`dockerfile:` is relative to the build context, which is the repo root."""
    if not HAS_SERVICES:
        path = path.replace("Docker/services/", "Docker/services.example/", 1)
    assert (REPO_ROOT / path).is_file(), f"compose builds {path}, which does not exist"


@pytest.mark.parametrize("path", _env_files())
def test_every_env_file_has_a_template(path):
    """env/ is gitignored; env.example/ is what a clone gets. A new env_file with
    no template there is a file a fresh checkout can neither find nor create."""
    name = Path(path).name
    assert (DOCKER_DIR / "env.example" / name).is_file(), (
        f"{path} is listed under env_file: but Docker/env.example/{name} does not exist"
    )


@pytest.mark.skipif(not HAS_SERVICES, reason="services/ not copied from the template yet")
def test_services_example_mirrors_services():
    """Same set of files, so the template cannot silently miss a config."""
    def files(root: Path) -> set[Path]:
        return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}

    assert files(DOCKER_DIR / "services") == files(DOCKER_DIR / "services.example")


def test_the_compose_substitution_templates_exist():
    """These three are read through --env-file, never env_file:, so the check
    above cannot see them."""
    for name in (".env.nginx", ".env.mongo", ".env.postgres"):
        assert (DOCKER_DIR / "env.example" / name).is_file(), name


def test_the_postgres_variables_are_required():
    """An unset ${POSTGRES_PASS} otherwise becomes an empty string without a word
    and the database initialises with a blank password."""
    text = _compose_text()
    for var in ("POSTGRES_USERNAME", "POSTGRES_PASS", "POSTGRES_MAIN_DB"):
        assert "${" + var + ":?" in text, f"{var} is not marked required in compose"


def test_the_example_redis_conf_carries_no_real_password():
    conf = (DOCKER_DIR / "services.example" / "redis" / "redis.conf").read_text()
    assert re.search(r"^requirepass\s+REPLACE_ME", conf, re.MULTILINE)
