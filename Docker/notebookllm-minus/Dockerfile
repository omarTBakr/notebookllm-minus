# NotebookLLM-minus — production image.
#
# Build context is the *repository root*, not this directory, because the
# application lives in src/ and the lockfile with it. From Docker/:
#
#     docker build -f notebookllm-minus/Dockerfile -t notebookllm-minus ..
#
# Base image choice: python:3.12-slim (Debian) rather than Alpine, and the uv
# binary copied in rather than installed with a shell script.
#
#   * Debian, not Alpine — Alpine is musl, so PyPI's manylinux wheels do not
#     apply and asyncpg, pydantic-core and the tokenizers under langchain all
#     compile from source. That trades a ~30 MB saving for a build measured in
#     tens of minutes and a compiler toolchain in the image.
#   * uv copied from its own published image at a pinned tag, rather than
#     `curl | sh` — the installer needs curl, which slim does not ship, and an
#     unpinned installer is a different uv on every rebuild.
#   * Multi-stage — uv, the lockfile and the build cache stay in the builder.
#     The runtime carries the virtualenv, the source, and nothing else.

# One place to change the interpreter, so the two stages cannot drift apart.
ARG PYTHON_IMAGE=python:3.12-slim

# --- builder ------------------------------------------------------------------

FROM ${PYTHON_IMAGE} AS builder

# Pin uv. A moving tag is a different resolver on every rebuild, which is the
# one thing a lockfile exists to prevent. Only the `uv` binary: `uvx` runs
# throwaway tools and nothing here does that.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/

# UV_COMPILE_BYTECODE   — pre-compile the venv, so startup is not the first
#                         import of several thousand modules.
# UV_LINK_MODE=copy     — the cache mount and /app are different filesystems;
#                         without this uv warns and falls back on every install.
# UV_PYTHON_DOWNLOADS=0 — use the interpreter already in this image rather than
#                         fetching a second one.
#
# Comments sit above the instruction rather than inside the line continuation:
# a `#` between `\` continuations is a parser edge case not worth relying on.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies alone, in their own layer. Bind-mounted rather than COPYed so
# the manifests do not become part of the layer, and cache-mounted so a rebuild
# after a code change reuses every downloaded wheel.
#
# --no-install-project is not an optimisation here, it is required: the project
# declares no [build-system], so it is not an installable package. The app runs
# as plain modules from WORKDIR, and only its dependencies go in the venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=src/uv.lock,target=uv.lock \
    --mount=type=bind,source=src/pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# NLTK's punkt model ships as data, not code, and TextProcessingController
# fetches it on the first split. Downloading it here turns a ~15 MB network
# call on the first upload — which fails outright on an offline host — into a
# build-time cost that is cached with the layer.
RUN /app/.venv/bin/python -m nltk.downloader -d /usr/share/nltk_data punkt_tab

# --- runtime ------------------------------------------------------------------

FROM ${PYTHON_IMAGE} AS runtime

# PYTHONUNBUFFERED        — logs reach the docker log driver as they happen.
# PYTHONDONTWRITEBYTECODE — the venv is already compiled above; this only stops
#                           stray __pycache__ appearing under /app at runtime.
# PATH                    — run out of the virtualenv without activating it.
# NLTK_DATA               — where the punkt model copied below lives.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    NLTK_DATA=/usr/share/nltk_data

# Unprivileged. Created before the copies so ownership is set by --chown as
# they land, rather than by a later chown layer duplicating the whole venv.
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder /usr/share/nltk_data /usr/share/nltk_data

# The application. Only src/ — the repository root also holds tests, Docker
# and the local .venv, none of which belong in a runtime image.
COPY --chown=app:app src/ /app/

# Written to at runtime: rotating logs, and the on-disk asset directory the
# file controller uses. Both are excluded from the build context, so they are
# created here — `install -d` sets the mode and owner in the one command.
RUN install -d -o app -g app /app/logs /app/assets

USER app

EXPOSE 8000

# Hits the app's own health route. Uses urllib rather than curl, which slim
# does not ship and which is not worth a layer to add. Defined here rather than
# only in compose so `docker run` gets it too; compose inherits this one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/base/health', timeout=4).status == 200 else 1)"]

# No --reload, no --workers: the lifespan builds one provider cache and runs
# Alembic under an advisory lock, and the metrics counters are per-process, so
# scaling is a matter of more containers, not more workers inside one.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
