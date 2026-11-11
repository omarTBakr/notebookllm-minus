# Tests and code quality

## What it does

Keeps the whole suite runnable on a laptop with no network, no model and no database, and keeps
formatting and layering from drifting.

## How it works

```bash
python -m pytest -q                     # from the repository root (pytest.ini sets the paths)
node --test tests/frontend/*.test.mjs   # the UI's pure modules
```

From `src/`, with the project's environment:

```bash
uv run pytest -q ../tests -p no:cacheprovider --ignore=../tests/frontend; echo "exit=$?"
```

**Read the exit code, not the last lines.** Piping a run through `tail` hides the `FAILED` lines.

| Folder | Covers |
| --- | --- |
| `tests/api/` | routes through the real app: auth, isolation, uploads, messages, studio, pictures |
| `tests/chatting/` | planning, reranking, citations, pictures and links in an answer |
| `tests/ingestion/` | chunking, layout, batched ingest, spreadsheets, OCR (`ocr/`), figures and links |
| `tests/guardrails/` | the middleware, and micro-batching against a real in-process worker |
| `tests/persistence/`, `tests/contracts/` | both database backends against one contract; schemas |
| `tests/providers/`, `tests/observability/`, `tests/tasks/`, `tests/domain/` | catalogues, tracing and pricing, jobs, pure logic |
| `tests/configuration/` | env examples against `Settings`, in both directions; compose paths |
| `tests/architecture/test_layering.py` | fails the build if a tier imports one it should not |
| `tests/frontend/` | progress, markdown, citation labels, attachments, the auth form (`node --test`) |

- **Fakes, not mocks of the world** (`tests/support/`). `db.py` is an in-memory `DbProvider`,
  `llm.py` holds streaming and planning chat clients, and `ingest.py` runs what an upload queued
  by calling the real stage bodies, so batching, page order and the completion claim are the
  production code.
- **Settings are rebuilt for every test** (`tests/conftest.py` clears the `get_settings` cache).
  Modules that bound a `Settings` object at import, `celery_app.SETTINGS` among them, keep the
  first one. A fixture that flips a switch the ingest stages read must flip it on that object
  too; `tests/api/test_images.py`'s `multimodal` fixture shows how.
- **The isolation guard** (`tests/api/test_isolation.py`) walks `app.routes`, so a new route
  without the login layer fails the suite.

### Code quality

Black and Ruff are configured in `src/pyproject.toml`. The repository's pre-commit hook runs them
on staged Python files. Activate it once per checkout:

```bash
git config core.hooksPath .githooks
```

By hand, from the repository root:

```bash
black --config src/pyproject.toml src
ruff check --config src/pyproject.toml src
```

**Always pass `--config`.** Black run on `tests/` without it reformats at 88 columns.

## Diagrams

The diagrams in [`software_engineering/`](../../software_engineering/README.md) are updated and
re-rendered in the same change as the code they describe (`bash software_engineering/render.sh`).

## Known limits

- `tests/guardrails/test_guardrail_integration.py::test_a_lone_request_is_flushed_by_the_timer_not_held_for_the_batch`
  asserts a wall-clock bound and can fail when the disk is busy. It passes on a re-run.
- `/memory` has no tests of its own.
- Both in [cleanup follow-ups](../roadmap/cleanup_followups.md).
