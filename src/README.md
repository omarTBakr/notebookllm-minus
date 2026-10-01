# `src/` — how this is laid out

A map of the tiers and the rules they follow, written because the layout is
now deep enough that "where does this go?" has a real answer and it was only in
people's heads.

## The tiers

Three tiers, each talking only to the one below it, plus `shared/` for
cross-cutting vocabulary and infrastructure that all three depend on but that
belongs to none of them.

| tier | package | answers |
| --- | --- | --- |
| presentation | `presentation/routes/` | HTTP: what a request means, what comes back. Thin — the work is elsewhere. |
| presentation | `presentation/web/` | The browser app: static CSS/JS and Jinja partials. |
| presentation | `presentation/middleware/` | Cross-cutting request handling. |
| application | `application/services/` | The work. Grouped by responsibility: `account`, `conversation`, `core`, `ingest`, `llm`, `memory`, `rag`, `studio`. Routes call these and never touch `data/`; `presentation/dependencies.py` is the one place that turns a request into services, and the only place that reads `app.db` or `app.providers` (`test/unit/test_layering.py` enforces both). |
| application | `application/providers/` | The LLM vendor adapters: `chatting/`, `embedding/`, plus the cache and settings-mapping glue. |
| application | `application/tasks/` | Background work: `jobs` (what Celery runs), `tracking` (progress and outcome), `workflows` (the ids a run is published under). |
| application | `application/prompts/` | Chat and studio prompts, one file per group per locale. A missing key raises rather than silently falling back. |
| application | `application/ocr_prompts/` | Ingest-correction prompts, per locale. Repairing a page, where an unknown language falls back to English. |
| application | `application/arabic_extraction/` | Reading a page that has no usable text layer, plus the benchmark that chose the engine. Off the default ingest path. |
| data | `data/models/` | Pydantic documents, plus the thin `XModel(db)` adapters. What the database holds. |
| data | `data/repositories/` | Building the things that vary: Postgres or Mongo, behind one interface per document type. |
| shared | `shared/enums/` | Fixed vocabularies, grouped by what they describe: `app`, `content`, `providers`, `storage`, `tasks`. |
| shared | `shared/utils/` | Settings, logging, metrics, and the pure readers: model ids, model capabilities, provider error text, host resources. |
| shared | `shared/exceptions.py` | The domain exception hierarchy every layer raises and one handler in `main.py` turns into a response. |

`main.py` and `celery_app.py`/`celery_queues.py` sit at `src/` root, outside
every tier — they're the two composition roots (API process, worker process)
that wire presentation/application/data/shared together, not code that
belongs to any one of them.

## Subpackages are an internal filing system

Every grouped package re-exports through its top-level `__init__.py`, and
callers import from there:

```python
from application.services import NLPService          # yes
from application.services.rag import NLPService       # no
```

The point is that a module can move between categories without touching its
callers. `shared/enums`, `application/services` and `application/tasks` all
work this way. Deep imports do exist where a caller wants a module rather
than a name — a test reaching for a private helper, say — and those pay the
cost of moving when it moves.

## Naming

**Two conventions are in use, split by package rather than by content**, and
this is the honest description rather than a rule anyone chose:

- **PascalCase**, file named for the single class it exports: everything in
  `application/services/` (`QuizService.py`), and the provider classes in
  `application/providers/chatting/` and `application/providers/embedding/`
  (`NvidiaChatProvider.py`).
- **snake_case** everywhere else, including `data/repositories/*/chunk_repository.py`
  — which also exports exactly one class, so the split is not "one class means
  PascalCase".

Left as it is deliberately. Converting either way is around thirty file renames
plus their imports, for no behavioural gain, and the risk is not zero: a moved
module that Celery registers by path takes its queue down silently. **New files
follow the package they land in** — a service is `ThingService.py`, a
repository is `thing_repository.py`.

## Two things that bite

**Celery registers task modules by path.** `celery_app.py` has an
`include=[...]` list; a job module missing from it never registers, and the
queue it was meant to serve then accepts messages nothing consumes — with no
error, ever. Task *names* are declared explicitly, so a module can move without
invalidating messages already queued, but the include list must follow it.

**A repository lands on both backends or neither.**
`test/db/test_backend_contract.py` compares the Postgres and Mongo
implementations method for method; adding one to a single backend fails there
rather than in production on whichever machine runs the other. The comparison is
driven by a hand-maintained `PAIRS` list, so a new repository also has to be
added *there* — nothing fails if you forget, which is the one gap in that file.

**Coordination between task stages lives in the database, not in Celery.**
Ingestion fans a document out into page batches and has to know when they are
all back. That was a chord, and on Celery 5.6.3 a chord whose header is a group
of chains marked one member of twenty-three — the callback never fired and a
whole book produced no chunks, silently. It is now a row count over
`ingest_batches` and one atomic claim on `ingest_runs`. Prefer a row you can
query over a callback you cannot see.
