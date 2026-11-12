# Studio

## What it does

Generates a **mind map**, **flashcards**, a **quiz** and summaries from a notebook's own sources.
Every node, card and question carries the chunk it came from and opens that page, passage or
video moment.

## How it works

- **Summaries, never raw text.** A 222-page book is 694 chunks and about 470k characters, which
  does not fit a context window; as one-line summaries it is about 35k tokens, which does. So the
  model is given summaries, and each item carries the `chunk_order` of the summary it came from,
  the only thread back to a real page.
- **`SummaryService`** (`src/application/services/studio/SummaryService.py`) writes one-line
  summaries onto the chunks, a batch per model call. Ingestion runs it after a document is stored
  (`INGEST_SUMMARISE`), Studio runs it for whatever is still missing, and whichever runs first
  saves the other the call.
- **One streaming loop** (`src/application/tasks/jobs/studio.py`, `celery-studio`). The first
  batch produces items in seconds and the set fills while it is being read; items are appended in
  the database and the browser sees them on its next poll. A second run does no summarising, and
  a worker killed mid-run loses only the batch in flight.
- **`ArtifactService`** holds what the kinds share: numbering the summaries, calling the model,
  translating the answer back into real citations, refusing what cannot be traced.
  `FlashcardService`, `QuizService` and `MindMapService` each declare their schema, prompt and
  item field.
- **Quiz answers** are written by the model as text and matched to an option by code, because a
  model asked for the index of the right answer was mostly wrong.
- Output is schema-validated structured output; see
  [models and providers](07_models_and_providers.md#structured-output).
- A repeated request returns the run already going instead of starting a second.

## API

| Method | Path | |
| --- | --- | --- |
| `POST` | `/chat/chats/{chat_id}/studio/{kind}` | generate `mindmap`, `flashcards` or `quiz` (`202`, or `200` with the run already going) |
| `GET` | `/chat/chats/{chat_id}/studio/{kind}` | the latest set, with progress and, when it failed, the reason |

## Data

`artifacts` (kind, status, items, error), migration `0009`; `chunk_summary` on chunks, `0008`.

## Settings

`INGEST_SUMMARISE` and `CELERY_QUEUE_STUDIO`. Generation uses the notebook's chat model.

## Tests

`tests/tasks/test_studio.py`, `test_ingest_summarise.py`, `tests/api/test_studio.py`,
`tests/domain/test_mind_map.py`, `tests/contracts/test_quiz_schema.py`, `tests/frontend/artifact_items.test.mjs`.

## Diagrams

[Studio generation](../../software_engineering/3_sequence_diagrams/studio_generation.png) · [user stories](../../software_engineering/1_user_stories/05_memory_and_studio.png) · [use cases](../../software_engineering/2_use_case_diagrams/studio_and_memory.png)

## Known limits

Audio overview, slide deck, video, reports, infographic and data table tiles are placeholders:
[roadmap](../roadmap/studio_audio_slides_video_reports.md).
