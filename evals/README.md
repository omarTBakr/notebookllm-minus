# evals/: answer quality (Ragas) and load (Locust)

Two tools that test the **running system** from the outside, over HTTP, the way a browser uses it.
Neither imports application code. They have an environment of their own (`evals/pyproject.toml`),
so the application's dependencies and Docker image do not carry them.

```bash
cp evals/.env.example evals/.env      # the stack's URL, a test account, the judge model
uv sync --project evals               # once
```

Both read `evals/.env`. The test account is signed up on first use.

## Answer quality: `ragas_eval.py`

```bash
uv run --project evals python evals/ragas_eval.py                       # the sample dataset
uv run --project evals python evals/ragas_eval.py --limit 3 --answers-only    # a quick check, no judge
uv run --project evals python evals/ragas_eval.py --notebook <chat_id>  # reuse a notebook, skip the upload
uv run --project evals python evals/ragas_eval.py --min faithfulness=0.8 --min context_recall=0.7
```

It makes a notebook, uploads the dataset's documents, waits for indexing, asks every question
through the streaming endpoint, reads back the passages each answer cited, and has a judge model
score the result. The report goes to `evals/reports/ragas_<time>.json` (and `ragas_latest.json`):
a summary, then every question with its answer, passages, timings and scores.

| Metric | Asks | Needs a reference |
| --- | --- | --- |
| `faithfulness` | Is every claim in the answer supported by the cited passages? | no |
| `answer_relevancy` | Does the answer address the question? | no |
| `context_precision` | Are the useful passages ranked above the useless ones? | yes |
| `context_recall` | Do the passages contain what the reference answer needs? | yes |
| `factual_correctness` | Does the answer state the same facts as the reference? | yes |

Low `context_recall` points at retrieval (chunking, embedder, `top_k`). Low `faithfulness` with
good recall points at the answer model or the prompt. Low `context_precision` points at ranking,
which is the reranker's job.

`--min METRIC=SCORE` makes the run exit 1 when a mean falls below it, so it can gate a release.

**The judge** is set in `evals/.env`: a local Ollama model (`RAGAS_JUDGE_BACKEND=ollama`), or any
OpenAI-compatible API (`openai`, with `RAGAS_JUDGE_BASE_URL` and `RAGAS_JUDGE_API_KEY`). Scores
from different judges are not comparable; keep one judge for a series of runs. A small local
judge is slow and sometimes fails to return valid output, which shows as fewer answers "scored"
than asked.

### Your own dataset

```
evals/datasets/<name>/
  documents/          the sources to upload (pdf, txt, md, csv, xlsx)
  questions.jsonl     {"question": "...", "reference": "..."} per line
```

`reference` is the answer a person would accept. Write questions whose answer is in the
documents and nowhere else, or the score measures the model's memory instead of the system.
`datasets/sample/` is an invented handbook with twelve questions.

## Load: `locustfile.py`

```bash
# with the web UI at http://localhost:8089
uv run --project evals locust -f evals/locustfile.py --host http://localhost:8081

# headless, with numbers to compare between runs
uv run --project evals locust -f evals/locustfile.py --host http://localhost:8081 \
    --headless --users 20 --spawn-rate 2 --run-time 3m --tags browse search \
    --csv evals/reports/load --html evals/reports/load.html
```

Each simulated user signs in once and then works in a notebook. Tasks are tagged:

| Tag | Does | Loads |
| --- | --- | --- |
| `browse` | open a notebook, list sources and messages, poll ingest progress | the API, auth, the database |
| `search` | `POST /nlp/index/search` | the embedder and the vector store |
| `ask` | a full streamed answer | planner, retrieval, reranker, the chat model |
| `ingest` | upload a small unique note | the Celery ingest pipeline |

Start with `--tags browse search` to find what the API and stores take. Add `ask` with a few
users: an answer holds a connection for its whole length and is bounded by the model, not the
app. A streamed answer shows as three rows: `stream opened` (headers only), `answer: first token`
and `answer: complete`.

Set `LOAD_CHAT_ID` to a notebook that already has sources (a `ragas_eval.py` run prints one) and
every user shares it. Leave it empty and each user makes a notebook and uploads the sample
document on start, which is itself an ingest load test; that wait shows as `upload until answerable`.

**Do not point either tool at a deployment other people are using** without meaning to: both
create notebooks, and the load test creates many requests.
