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


def _compose_text() -> str:
    return COMPOSE.read_text()


def _mount_sources() -> list[str]:
    """`- ./services/x:/target[:ro]` entries. Named volumes (no ./) are skipped."""
    return re.findall(r"^\s*-\s*(\./[^\s:]+):", _compose_text(), re.MULTILINE)


def _env_files() -> list[str]:
    """`- ./env/.env.x` entries under an `env_file:` key, short or long form
    (`- path: ./env/.env.x` with `required: false`)."""
    return re.findall(r"^\s*-\s*(?:path:\s*)?(\./env/[^\s]+)\s*$", _compose_text(), re.MULTILINE)


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
    assert (DOCKER_DIR / source).exists(), f"compose mounts {source}, which does not exist"


@pytest.mark.parametrize("source", _mount_sources())
def test_every_mount_lives_under_services(source):
    assert source.startswith("./services/"), (
        f"{source}: a service's config belongs under Docker/services/<name>/"
    )


@pytest.mark.parametrize("path", _dockerfiles())
def test_every_dockerfile_exists(path):
    """`dockerfile:` is relative to the build context, which is the repo root."""
    assert (REPO_ROOT / path).is_file(), f"compose builds {path}, which does not exist"


@pytest.mark.parametrize("path", _env_files())
def test_every_env_file_has_a_template(path):
    """The real env files are tracked in the private repo only; the public repo
    ships env.example/. A new env_file with no template there is a file a public
    checkout can neither find nor create."""
    name = Path(path).name
    assert (DOCKER_DIR / "env.example" / name).is_file(), (
        f"{path} is listed under env_file: but Docker/env.example/{name} does not exist"
    )


def test_services_example_mirrors_services():
    """Same set of files, so the public copy cannot silently miss a config."""
    def files(root: Path) -> set[Path]:
        return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}

    assert files(DOCKER_DIR / "services") == files(DOCKER_DIR / "services.example")


def _env_keys(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0].strip()
        for line in path.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


def test_no_compose_command_needs_env_file_flags():
    """Every ${...} left in the compose file has a default.

    A substitution without one has to come from the shell or --env-file, and
    that is what made every command -- `ps` and `logs` included -- need three
    --env-file flags, and a run without them fail. Settings a container needs
    go through env_file:, which compose loads by itself. $$VAR is the
    container's own shell variable, not a compose substitution.
    """
    code = "\n".join(line.split("#", 1)[0] for line in _compose_text().splitlines())
    substitutions = re.findall(r"(?<!\$)\$\{([^}]+)\}", code)

    assert substitutions, "found no substitutions at all, so this checks nothing"
    for expression in substitutions:
        assert ":-" in expression, f"${{{expression}}} has no default, so a command without flags fails"


def test_the_compose_dotenv_holds_no_secrets():
    """Docker/.env is read by compose automatically and is safe to publish;
    credentials belong in env/, which the public repository never receives."""
    assert _env_keys(DOCKER_DIR / ".env") <= {"NGINX_PORT"}


def test_postgres_credentials_use_the_images_own_names():
    """pgvector reads env/.env.postgres directly, so the keys are the image's.
    They are also .env.app's names for the same server: the old POSTGRES_PASS
    spelling was one letter from the name everything else used."""
    assert _env_keys(DOCKER_DIR / "env.example" / ".env.postgres") == {
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    }


def test_the_example_redis_conf_carries_no_real_password():
    conf = (DOCKER_DIR / "services.example" / "redis" / "redis.conf").read_text()
    assert re.search(r"^requirepass\s+REPLACE_ME", conf, re.MULTILINE)
