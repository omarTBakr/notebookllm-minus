# Memory

## What it does

`/memory I prefer short answers` stores a durable fact about the user. Stored facts are folded
into every later answer, in any notebook, whether or not the notebook has sources.

## How it works

- **The command** (`src/presentation/routes/chat/memory.py`). The route recognises `/memory`,
  claims an idempotency key, queues the extraction and answers at once with an acknowledgement.
- **Extraction on a worker** (`src/application/tasks/jobs/memory.py`, `celery-memory`). One
  structured call (`generate_structured`) turns the message into keyed facts; they are embedded
  and stored, and the real answer ("Saved: diet, job title") is appended as a second assistant
  message, which the browser's poller picks up.
- **`MemoryService`** (`src/application/services/memory/MemoryService.py`). `facts` is a
  *required* field of the schema: with a default, a model that echoes the schema back parses as a
  clean, silent zero facts and the repair loop never fires.
- **At answer time** `ChatService` searches the user's facts for the ones closest to the question
  and adds them to the prompt. They are surfaced in the `meta` event as `memories`, the way
  document hits are surfaced as `citations`.
- **The composer's memory button** runs a ranked search of the current draft against stored facts.

## API

| Method | Path | |
| --- | --- | --- |
| `POST` | `/chat/chats/{chat_id}/message` | a message starting `/memory` is stored, not answered |
| `GET` | `/chat/chats/{chat_id}/memory?q=` | stored facts closest to *q* |

## Data

`personal_user_information` (`user_id`, `key`, `description`), migration `0011`. Each fact's
vector lives in a per-user collection in the vector store.

## Settings

`CELERY_QUEUE_MEMORY` names the queue; the extraction uses the notebook's chat model with
thinking off.

## Tests

**None yet.** `/memory` has no tests of its own; `tests/support/db.py` only carries the fake
repository. Listed in [cleanup follow-ups](../roadmap/cleanup_followups.md).

## Diagrams

[/memory](../../software_engineering/3_sequence_diagrams/memory.png) · [user stories](../../software_engineering/1_user_stories/05_memory_and_studio.png) · [use cases](../../software_engineering/2_use_case_diagrams/studio_and_memory.png)

## Known limits

- **Postgres only.** On the mongo backend `personal_user_info()` raises `NotImplementedError`:
  [memory on Mongo](../roadmap/memory_on_mongo.md).
- There is no way to list or forget a stored fact from the UI yet.
