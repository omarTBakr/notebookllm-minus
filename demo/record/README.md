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

Account · notebook · sources (files, then links) · a PDF citation · a spreadsheet row · a video
moment · an Arabic question · pictures (the chart retrieved, then a picture attached) · an agentic
answer · guardrails · `/memory` · the model picker · Studio (mind map, flashcards, quiz) · theme
and Arabic · Visualization (dashboards, traces, tasks, queues).

- `--only pdf,studio` records a subset (the account scene always runs). `--no-links` skips the
  link sources, for a run without network. `--headed` shows the browser.
- **A scene that cannot be shown is skipped, with the reason printed and saved in the timeline.**
  Pictures need `MULTIMODAL_ENABLED`, the refusal needs `GUARDRAILS_ENABLED`, Visualization needs
  `VISUALIZATION_ENABLED`, traces need the `langfuse` profile. Nothing is staged.
- Captions are drawn in the page while recording, so they are part of the picture. During a wait
  the caption says "fast-forwarded", and `render.py` compresses that stretch to a couple of seconds.

`out/` is gitignored. Review the result before replacing the files in `demo/`:

```bash
uv run --project evals --group demo python demo/record/render.py --to demo/record/out/preview
```
