# NotebookLLM-minus: instructions for Claude

Project skills live in `.claude/skills/`. Use them instead of rediscovering the steps:

| Skill | Use for |
| --- | --- |
| `nlm-stack` | bringing up, rebuilding or checking the Docker Compose stack on this machine (project name, port clashes, stale drive share, slow builds) |
| `nlm-evals` | the Ragas evaluation and Locust load test in `evals/`, and recording their numbers |
| `nlm-demo-recording` | re-recording `demo/walkthrough.mp4` / `.gif` with `demo/record/` |
| `nlm-release` | verifying (tests by exit code, frontend, Black, Ruff, Actions), pushing to the **public** `upstream`, merging, releasing |

Ground rules for this repository:

- Push, merge and release target the public repo (`upstream`, omarTBakr/notebookllm-minus), never `origin`.
- Live env files (`Docker/env/`, `Docker/.env`, `src/.env`, `evals/.env`) are never committed and are not edited without asking.
- Judge a test run by its exit code, never by piping it through `tail`.
- UML in `software_engineering/` and pages in `docs/features/` change in the same commit as the feature.
- Black and Ruff always with `--config src/pyproject.toml` (or `--config pyproject.toml` from `src/`).
