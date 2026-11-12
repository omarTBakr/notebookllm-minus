# Evaluation and load testing

## What it does

Tests the **running system** from the outside, end to end. Ragas scores how good the answers
are: whether they are supported by the passages they cite, whether retrieval found what was
needed, whether they match a reference answer. Locust loads the system with many signed-in users
at once and reports throughput, latency percentiles and failures.

The unit suite ([tests and code quality](16_tests_and_code_quality.md)) runs on fakes and proves
the code does what it says. These two prove the deployed stack, with its real models, stores and
workers, does what a user needs.

## How it works

Everything is under [`evals/`](../../evals/README.md), a small uv project with its own
environment, so the application's dependencies and image do not carry Ragas or Locust.

| File | Holds |
| --- | --- |
| `evals/notebook_client.py` | a plain HTTP client: sign in, make a notebook, upload, wait for indexing, ask and read the event stream, read a cited passage back |
| `evals/ragas_eval.py` | the evaluation run and its report |
| `evals/locustfile.py` | the simulated user and its tagged tasks |
| `evals/datasets/sample/` | an invented handbook and twelve questions with reference answers |

### Answer quality (Ragas)

`ragas_eval.py` signs in, makes a notebook, uploads the dataset's documents, waits for the
workers, and asks each question through `POST /chat/chats/{id}/message`, the same stream the
browser reads. A citation carries where a passage is, not its text, so the passages are read
back through `/locate`, as the viewer does on a click. A judge model then scores each record.

| Metric | Asks | Points at |
| --- | --- | --- |
| `faithfulness` | is every claim supported by the cited passages? | the answer model and prompt |
| `answer_relevancy` | does the answer address the question? | the answer model and planner |
| `context_precision` | are useful passages ranked above useless ones? | the reranker |
| `context_recall` | do the passages hold what the reference needs? | chunking, the embedder, `top_k` |
| `factual_correctness` | same facts as the reference? | the whole chain |

Each metric runs over the records it applies to: one that needs a reference skips lines without
one, and a question with no answer or no passages is counted as unanswered, not scored as zero.
`--min METRIC=SCORE` exits 1 when a mean is below it.

The judge is a local Ollama model or any OpenAI-compatible API. **Scores from different judges
are not comparable**; keep one for a series of runs.

### Load (Locust)

One user class, `NotebookUser`: it signs in once, then mostly reads and sometimes asks.

| Tag | Does | Loads |
| --- | --- | --- |
| `browse` | open a notebook, list sources and messages, poll ingest progress | the API, auth, the database |
| `search` | `POST /nlp/index/search` | the embedder and the vector store |
| `ask` | a full streamed answer | planner, retrieval, reranker, the chat model |
| `ingest` | upload a small unique note | the Celery ingest pipeline |

Locust times a request when its response starts, which for a stream is the headers. So an answer
is reported as three rows: `stream opened`, `answer: first token` and `answer: complete`.

## Running it

```bash
cp evals/.env.example evals/.env && uv sync --project evals

uv run --project evals python evals/ragas_eval.py --min faithfulness=0.8

uv run --project evals locust -f evals/locustfile.py --host http://localhost:8081 \
    --headless --users 20 --spawn-rate 2 --run-time 3m --tags browse search \
    --csv evals/reports/load --html evals/reports/load.html
```

## Settings

In `evals/.env`, not the application's settings.

| Setting | Does what |
| --- | --- |
| `EVAL_BASE_URL` | the running stack (`http://localhost:8081` through nginx) |
| `EVAL_EMAIL` / `EVAL_PASSWORD` | a test account, signed up on first use |
| `RAGAS_JUDGE_BACKEND` / `RAGAS_JUDGE_MODEL` / `RAGAS_EMBED_MODEL` | `ollama` or `openai`, and the judge's chat and embedding models |
| `RAGAS_JUDGE_BASE_URL` / `RAGAS_JUDGE_API_KEY` | where the judge is, and its key for a hosted one |
| `LOAD_CHAT_ID` | a notebook every simulated user shares; empty = each makes its own |

## Tests

`tests/evals/test_eval_tooling.py` covers the pure parts: stream parsing, the questions file,
thresholds and means. The tools themselves need a live stack and are not part of the suite.

## Diagrams

[Evaluation and load](../../software_engineering/3_sequence_diagrams/evaluation_and_load.png)

## First measurements

Measured on 2026-11-12 against the local compose stack, as a first baseline. Small sample; read
them as "the loop works", not as a benchmark.

**Ragas**, sample dataset, judge `llama3.1:8b` with `nomic-embed-text` on local Ollama. All
twelve questions were answered with cited passages, and all twelve were scored on every metric.

| Metric | Mean |
| --- | --- |
| `faithfulness` | 0.944 |
| `answer_relevancy` | 0.903 |
| `context_precision` | 0.968 |
| `context_recall` | 0.931 |
| `factual_correctness` | 0.832 |

Ingesting the document took 115 s from upload to answerable. A question took 13 s on average.

**Locust**

| Run | Requests | Failures | Median | 95th percentile |
| --- | --- | --- | --- | --- |
| `browse` + `search`, 8 users, 45 s | 203 | 0 | 9 ms | 59 ms |
| `search` alone, within that run | 29 | 0 | 20 ms | 30 ms |
| `ask`, 2 users, 75 s: answer complete | 7 | 0 | 13 s | 22 s |

**The first token arrives with the last.** In both tools the time to the first piece of answer
text equals the time to the end of the answer, to within a few hundredths of a second: the
answer reaches the client in one piece, not as a stream. The cause has not been investigated
(candidates: the output guardrail holding a short answer as a single passage, a proxy buffering
the response, or a chat backend that does not stream). Listed in
[cleanup follow-ups](../roadmap/cleanup_followups.md).

## Known limits

- `ragas` 0.4 imports a module `langchain-community` 0.4 removed, so `evals/` pins
  `langchain-community` below 0.4. The metric classes used work with any LangChain chat model
  and are deprecated for removal in `ragas` 1.0, so `ragas` is pinned below 1.0 too.
- A small local judge is slow and sometimes returns output Ragas cannot parse; those records
  show as not scored.
- The sample dataset is twelve questions on one short document: enough to prove the loop, too
  small to rank two configurations.
- Neither tool runs in CI: both need the stack and a model.
