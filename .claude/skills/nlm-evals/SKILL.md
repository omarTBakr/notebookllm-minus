---
name: nlm-evals
description: Run the Ragas answer-quality evaluation or the Locust load test in evals/ against the running NotebookLLM-minus stack, read the results, and record them in the docs. Use when the user asks to evaluate answers, test end to end, measure quality, load or stress test, or refresh the numbers in docs/features/17.
---

# Ragas and Locust against the running stack

Read `evals/README.md` for the full options. The stack must be up (see the nlm-stack skill).

## Setup, once
```bash
uv sync --project evals          # separate env: never add ragas/locust to src/
```
`evals/.env` (gitignored) holds `EVAL_BASE_URL`, a test account (`EVAL_EMAIL`/`EVAL_PASSWORD`, signed up on first use) and the judge. Generate test credentials into it; never echo them in chat.

## Ragas
```bash
uv run --project evals python evals/ragas_eval.py --answers-only --limit 2   # smoke: no judge
uv run --project evals python evals/ragas_eval.py                            # full, 12 questions
uv run --project evals python evals/ragas_eval.py --notebook <chat_id>       # reuse, skip upload
```
Run the full one in the background, logging to a file with `; echo exit=$?`. Results: `evals/reports/ragas_latest.json` (summary + every record). Judge: local `llama3.1:8b` + `nomic-embed-text` on Ollama by default. Scores from different judges are not comparable.

## Locust
```bash
LOAD_CHAT_ID=<chat_id> uv run --project evals locust -f evals/locustfile.py --host http://localhost:8081 \
  --headless --users 8 --spawn-rate 4 --run-time 45s --tags browse search --only-summary
```
Add `--tags ask` with 2 users for streamed answers. Read `answer: first token` and `answer: complete`, not the `stream opened` row (Locust times a stream at its headers).

## Gotchas already learned
- `ragas` 0.4 needs `langchain-community<0.4` (it imports a removed vertexai module). Pinned in `evals/pyproject.toml`; keep it.
- Never import `locust` and `ragas` in one process: gevent's patching hangs the import.
- A citation carries no passage text; contexts are read back through `/chat/chats/{id}/assets/{asset}/chunks/{n}/locate`.
- First import of ragas on this drive can take minutes.

## Record the numbers
Update the "First measurements" section of `docs/features/17_evaluation_and_load_testing.md` and the README's "Evaluation and load testing" tables, with the date, judge and run sizes. Never round a failure into a pass.
