# Recording the walkthrough

`demo/walkthrough.mp4` and `demo/walkthrough.gif` are produced by a script that drives a real
browser through the running app, so the video can be made again whenever the UI changes.

```bash
uv sync --project evals --group demo                        # once: Playwright
uv run --project evals --group demo playwright install ffmpeg   # once: Playwright's video helper (2 MB)

uv run --project evals --group demo python demo/record/walkthrough.py   # record
uv run --project evals --group demo python demo/record/render.py        # cut, speed up, write mp4 + gif
```

It uses the system's Chrome (no browser download) and the system's `ffmpeg` for rendering. The
account is `DEMO_EMAIL` / `DEMO_PASSWORD` in `evals/.env`, signed up through the real form on the
first run. A fresh notebook is made each time.

| File | Does |
| --- | --- |
| `walkthrough.py` | the scenes, in order; writes `out/walkthrough.webm` and `out/timeline.json` |
| `render.py` | plays every "wait" stretch fast, then writes the mp4 and a palette-matched gif |
| `make_assets.py` | builds the small uploaded sources in `assets/` (run with the app's environment) |
| `links.json` | the sources added by link; empty a value to leave that one out |

## Scenes

Grouped into topics, each opened by a white title card with the topic's name in bold black
(drawn in the page like the captions, and kept at normal speed by `render.py`):

Account · notebook · sources (files, a picture, then links) · **Multimedia**: pictures (the chart
retrieved and cited, then a picture attached) and a video moment · **Guardrails** · **Observability**:
Visualization, with each Grafana dashboard (FastAPI, RabbitMQ, PostgreSQL, host), Langfuse traces,
Flower tasks and RabbitMQ queues · **Answers you can check**: a PDF citation, a spreadsheet row, an
Arabic question · **Agentic research** · **Memory and models** · **Studio** (mind map, flashcards,
quiz) · **Theme and Arabic interface**.

Langfuse and RabbitMQ have sign-in pages of their own. Before the scenes, the recorder signs in to
both in a second, unrecorded page of the same browser, with `LANGFUSE_INIT_USER_*` and
`RABBITMQ_DEFAULT_*` read from `Docker/env/` (never printed), so their frames open on content.

- `--only pdf,studio` records a subset (the account scene always runs). `--no-links` skips the
  link sources, for a run without network. `--headed` shows the browser.
- **A scene that cannot be shown is skipped, with the reason printed and saved in the timeline.**
  Pictures need `MULTIMODAL_ENABLED`, the refusal needs `GUARDRAILS_ENABLED`, Visualization needs
  `VISUALIZATION_ENABLED`, traces need the `langfuse` profile. Nothing is staged.
- Captions are drawn in the page while recording, so they are part of the picture. Waits (a model
  answering, a worker indexing) are cut by `render.py`, which keeps only the last half second of
  each: the answer appearing.

`out/` is gitignored. Review the result before replacing the files in `demo/`:

```bash
uv run --project evals --group demo python demo/record/render.py --to demo/record/out/preview
```
