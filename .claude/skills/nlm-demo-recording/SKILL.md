---
name: nlm-demo-recording
description: Re-record the NotebookLLM-minus walkthrough video (demo/walkthrough.mp4 and .gif) with the scripted Playwright recorder in demo/record/, review it, and replace the files. Use when the user asks to update, redo or extend the demo video or GIF, or after a UI change the video should show.
---

# Recording the walkthrough

Read `demo/record/README.md`. The stack must be up with the features the scenes show switched on (see the nlm-stack skill): `MULTIMODAL_ENABLED`, `GUARDRAILS_ENABLED`, `RERANKER_ENABLED`, `LANGFUSE_ENABLED`, `VISUALIZATION_ENABLED` in `Docker/env/.env.app`.

## Once
```bash
uv sync --project evals --group demo
uv run --project evals --group demo playwright install ffmpeg   # Playwright's 2 MB video helper
```
`DEMO_EMAIL` / `DEMO_PASSWORD` live in `evals/.env`. The system Chrome is used (`channel="chrome"`), no browser download.

## Record, a few scenes first
```bash
uv run --project evals --group demo python demo/record/walkthrough.py --only pdf,studio --headed
uv run --project evals --group demo python demo/record/walkthrough.py            # all scenes
uv run --project evals --group demo python demo/record/render.py --to demo/record/out/preview
```
A scene whose feature is off or whose step fails is skipped and listed with the reason; it is never staged. Fix the cause or tell the user what is missing; do not ship a video with a skipped scene without saying so.

## Review before replacing
Extract stills and look at them (`ffmpeg -ss <t> -i out/preview/walkthrough.mp4 -frames:v 1 still.png`, then Read the png). Check captions, that each citation opened, that Visualization's frames are not blank. Only then render to `demo/` and update the README's demo section (alt text, numbered list).

## Notes
- Captions are drawn in the page, so they are part of the recording. Waits are cut by `render.py` (only their last 0.5 s is kept), so no wait shows in the video.
- Selectors come from the templates' ids (`#question`, `#btn-send`, `#btn-visualization`, `.sources-block__link`). After a UI change, run the affected scene with `--headed` first.
- Uploaded assets are built by `demo/record/make_assets.py` (run with the app's env, `cd src && uv run python ../demo/record/make_assets.py`).
