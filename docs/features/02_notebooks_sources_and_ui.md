# Notebooks, sources and the interface

## What it does

A user has sessions, a session has notebooks, a notebook has sources and a conversation. Sources
are files (PDF, txt, markdown, CSV, XLSX, pictures) or links (an online PDF, an article, a
YouTube video, a public Google Sheet). The web UI is three panels, Sources, Chat and Studio, in
English and Arabic.

## How it works

- **Conversation tree** (`src/application/services/conversation/`). `ConversationService` owns
  sessions and notebooks, `MessageService` the transcript, `SourceService` a notebook's sources.
  `chat_id` **is** the `project_id`, so a notebook reuses the ingestion path unchanged.
- **Attaching a file** (`services/ingest/AssetIngestService.py`). Validated on type and size and
  stored as bytes in the database; nothing touches disk. Identity is the sha256 of the contents,
  scoped to one notebook: the same file again is a `409` before anything is chunked, and renaming
  does not sneak it past. A partial unique index enforces it, so two racing uploads collide in
  the database.
- **Deleting a source** removes it, its chunks and its vectors, derived-first. Nothing cascades
  in either store, so a partial failure leaves a source visibly listed instead of orphan vectors.
- **Spreadsheets** are read row by row (`services/ingest/tabular.py`): one chunk per row, each
  value labelled with its column, cited as `Sheet1 · row 12`, numbered as in Excel.
- **Per-notebook settings**: models, temperature, output length, chunk size and overlap,
  highlight colour, and which sources are switched off for answers.

### Adding a link

Only public addresses are fetched; a private or non-http link is refused
(`services/ingest/UrlSourceService.py`). A YouTube video is read from its transcript and cited by
the moment it is spoken; a Google Sheet is read as CSV.

A pasted link takes one extra hop before it is an upload. The route checks the notebook
(`404` before anything is queued), claims the fetch under the usual idempotency key, publishes
`fetch_url_task` and answers `202` with its id. Pasting the same link again while that fetch is
running returns the same id rather than a second task. On the worker the task fetches the
link, attaches the bytes exactly as an upload would, and queues the ingestion chain.

What can go wrong with a link — unreachable, no transcript, a page that needs JavaScript, a
sheet that is not public, a duplicate of something already attached — is found by the task, so
it is reported on the task's row (`status = FAILURE`, `error` = the reason), which is what the
sources panel shows.

The fetch is the *first* of two tasks, so the progress bar has to hand over: when the fetch
succeeds, `GET /chat/chats/{id}/indexing` carries `next_task_id` (the ingestion it queued) and
the panel keeps polling that one, so there is a single bar from the paste to the last chunk
instead of "done" with nothing indexed yet.

### The interface

Jinja templates plus ES modules under `src/presentation/web/`, no build step. Markdown renders as
it streams. Panels resize by dragging and fold to a rail, remembered across reloads. English and
Arabic, including right-to-left layout. Answers can be copied, downloaded, or saved back into
Sources as a new document. Reasoning models stream their scratchpad into a collapsing panel:
shown, never stored. Generation can be stopped mid-answer and the partial reply is kept. Tiles
with no backend yet are marked *soon*.

## Settings

| Setting | Does what |
| --- | --- |
| `ALLOWED_TYPES` / `MAX_FILE_SIZE` | accepted upload types and the size ceiling |
| `URL_FETCH_TIMEOUT` | seconds a link fetch may take |
| `YOUTUBE_TRANSCRIPT_LANGUAGES` / `YOUTUBE_PROXY_URL` | transcript languages to try, and a proxy for hosts YouTube blocks |
| `CHAT_HISTORY_LIMIT` | prior turns sent as context |
| `DEFAULT_LANG` | the fallback interface and prompt language |

## API

| Method | Path | |
| --- | --- | --- |
| `GET/POST/PATCH/DELETE` | `/chat/users…`, `/chat/users/{id}/sessions`, `/chat/sessions/{id}/chats` | profile, sessions, notebooks |
| `POST` | `/chat/chats/{chat_id}/documents` | attach a file (`202`) |
| `POST` | `/chat/chats/{chat_id}/sources/url` | add a link (`202` with the fetch task's id) |
| `POST/GET/PATCH/DELETE` | `/chat/chats/{chat_id}/assets…` | a notebook's sources: list, content, rename, delete |
| `GET` | `/chat/chats/{chat_id}/messages` | transcript |
| `POST` | `/data/upload/{project_id}` | store a file as an asset (the older, notebook-less route) |

## Tests

`tests/api/test_chats.py`, `test_assets.py`, `test_documents.py`, `test_link_sources.py`,
`tests/domain/test_url_sources.py`, `tests/ingestion/test_tabular.py`, `test_validation.py`,
`tests/frontend/` (progress, markdown, citation labels, attachments).

## Diagrams

[Link source → fetch](../../software_engineering/3_sequence_diagrams/url_ingest.png) · [notebooks stories](../../software_engineering/1_user_stories/01_profiles_and_notebooks.png) ·
[sources stories](../../software_engineering/1_user_stories/02_sources_and_ingest.png) · [use cases](../../software_engineering/2_use_case_diagrams/sources_and_ingest.png)

## Known limits

- Private Google Sheets and more formats: [roadmap](../roadmap/private_google_sheets_and_more_formats.md).
- Copy, share and sort in the UI: [roadmap](../roadmap/ui_copy_share_apps_sort.md).
- Web-search grounding is stored per notebook and shown as *soon*: [roadmap](../roadmap/web_search_grounding.md).
