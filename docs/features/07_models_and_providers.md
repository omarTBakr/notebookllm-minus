# Models and providers

## What it does

Lets every notebook name its own chat and embedding model, from a picker built out of what is
actually installed and reachable. Seven chat and six embedding backends sit behind one interface
each; nothing above `src/application/providers/` names a vendor.

## How it works

- `src/application/providers/chatting/` and `providers/embedding/`: one interface, one class per
  vendor, one factory. `providers/provider_cache.py` (`ProviderCache`) builds and reuses clients
  per model id.
- `src/application/services/llm/ModelService.py` discovers models from every configured source;
  `NvidiaModelService.py` and `OpenRouterModelService.py` hold the hosted catalogues.
- `services/llm/structured_generation.py` (`generate_structured`) is the one way to ask a model
  for a shape.
- `services/llm/vision.py` (`has_vision`) answers whether a model can be shown pictures.

Each subsystem below is one abstract interface, one implementation per vendor, and a factory
turning a name from `.env` into a configured instance — the LLM ones under
`src/application/providers/`, storage under `src/data/repositories/`.

| Subsystem | Tier | Backends | Interface |
| --- | --- | --- | --- |
| `providers/chatting` | application | `anthropic`, `openai`, `google`, `cohere`, `nvidia`, `openrouter`, `ollama` | `generate_text(prompt, chat_history, max_tokens, temperature, json_schema)` |
| `providers/embedding` | application | `openai`, `google`, `cohere`, `nvidia`, `openrouter`, `ollama` | `embed(texts, input_type)` |
| `repositories` | data | `mongo` (+ Qdrant), `postgres` (+ pgvector) | twelve repository ABCs |

### Structured output

Every Studio set, summary, mind map and page repair is the same problem: make a model return
a shape. Each shape is a Pydantic model; `generate_structured` sends that model's JSON Schema
to providers that can constrain decoding to it, and still puts the schema in the prompt and
validates the result, so a backend that ignores the constraint behaves as it always did.

| Backend | How the schema is enforced |
| --- | --- |
| Ollama | `format=<schema>` — every token is constrained, for any model |
| Google | `response_json_schema` with a JSON MIME type |
| OpenAI, NVIDIA NIM, OpenRouter | `response_format: json_schema`; an endpoint that answers 400 is retried once without it, remembered per model |
| Anthropic, Cohere | not passed (prompt, validate, repair) |

Two limits matter in practice. The hosted endpoint may accept `response_format` and ignore it
for a given model — measured: `llama-3.2-11b-vision` on NVIDIA returned free-form output with
and without it, `nemotron-3-super` obeyed — so a model that cannot follow a schema is a
configuration problem the fallback only softens. And a list gets a `maxItems` in the schema
sent (`max_items`), not in the validating model, so a decoder cannot ramble past "at most 3"
while a backend that cannot constrain still returns something the caller can truncate.
Studio batches also carry a token cap and a per-attempt timeout, so one runaway attempt costs
seconds rather than a whole run.

**One neutral message format.** Callers build `{"role", "content"}` dicts using `ChatRole`
and each provider translates on the way out — Anthropic and Google lift the system turn into
a separate argument, Google renames `assistant` to `model`, the rest take it inline.
`generate_text()` is concrete on the interface: it normalises, logs, times and delegates to
`_generate_text()`, so no provider can forget to log and all of them log the same fields.

Normalising includes **forcing strict alternation** — user/assistant/user/… after the system
turn. Ollama accepts any shape; NVIDIA and Anthropic answer `400 Conversation roles must
alternate`. Two ordinary things here produce a shape they reject: a stream that failed or was
abandoned stored a question and never an answer, and history is a window of the last
`CHAT_HISTORY_LIMIT` messages that can open mid-exchange. Consecutive same-role turns are
joined rather than dropped, because an unanswered question is context for the next one.

**Streaming is per provider.** `stream_text()` is concrete on the interface and yields
`{"kind": "thinking" | "content"}` pieces; a provider that cannot stream inherits a fallback
that generates the whole answer and yields it once, so the endpoint works either way and only
latency differs. Ollama and the OpenAI wire format (so OpenAI and NVIDIA) stream for real.
Reasoning arrives on its own field — Ollama's `thinking`, `reasoning_content` on an
OpenAI-compatible server — which is why the scratchpad never has to be parsed back out of the
answer text.

**Embeddings are batch-first**, and two failures are caught at the boundary: a count mismatch
(vectors are zipped against chunk ids by position, so one dropped vector silently misaligns
everything after it) and a width mismatch against `EMBEDDING_MODEL_SIZE`, which would
otherwise surface as an error naming the collection rather than the misconfigured setting.

**`nvidia` is NVIDIA NIM, not a sixth SDK.** Its API *is* OpenAI's, so `NvidiaChatProvider`
is `OpenAIChatProvider` under another name — one line, plus a vendor label so its errors do
not say "OpenAI". It is a separate backend rather than `openai` with a different base URL
because the two are separate accounts with separate catalogues, and a NIM model id carries
its publisher (`meta/llama-3.2-11b-vision-instruct`). `NvidiaEmbeddingProvider` overrides
`_embed` for three real differences: its models are asymmetric (`input_type` is
`passage`/`query`), a request is capped at 256 inputs — under `CHUNKING_BATCH_SIZE`, so the
provider splits the batch itself — and the width is fixed, so `dimensions` is never sent.

**`openrouter` is the same idea, with a dialect.** One `OPENROUTER_API_KEY` (shared with the
OCR extractor of the same name) puts hundreds of publishers' models behind an OpenAI-compatible
API, so `OpenRouterChatProvider` is `OpenAIChatProvider` again, differing in two fields: the
cap is `max_tokens`, and the think toggle is `reasoning: {enabled}` rather than NIM's
`chat_template_kwargs`. Ids read `openrouter/<publisher>/<model>`; the publisher in the middle
may itself be a source name (`anthropic/...`), which is why only the first segment is a source.
Discovery needs no probing, unlike NVIDIA: `GET /models` already says what each model reads and
writes and which parameters it accepts, so vision, tools and reasoning are read off the listing
and image-output and guard models are dropped. Embedding models come from a separate listing
and are the only ones asked for their width.

No provider class holds a URL, a limit or a wire string. Endpoints and limits are `Settings`
fields; vendor spellings (`passage`, `END`) are tables in `shared/enums/providers/*_mappings.py`; and
which settings reach which constructor is one more table walked by `setting_kwargs()`.
Giving a provider a new knob is a line in that table plus a field on `Settings` — the
factories never branch per vendor.

### Choosing models

A notebook can name its own chat and embedding model, and the picker is built from what is
genuinely available rather than a hardcoded list. `ModelService` merges every configured
source and groups the result twice.

**By source** — `local` and `cloud` are two Ollama hosts, `nvidia` is a hosted vendor. A
model id carries its source (`local/gemma4:e4b`, `nvidia/meta/llama-3.2-11b-vision-instruct`)
and that prefix selects the *provider*, so one notebook can run on a local model while the
next runs on NVIDIA — something a single global `GENERATION_BACKEND` cannot express.

**By parameter size** — under 8B, 8B–30B, over 30B, sorted smallest first. Ollama reports the
count; NVIDIA does not, so it is read out of the tag, where nearly every model carries one. A
mixture-of-experts tag names its total before its active count (`120b-a12b` is a 120B model),
a version is not mistaken for a size, and a tag advertising nothing is filed under "size
unknown" rather than guessed at.

**Chat and embedding lists are disjoint by capability.** Ollama embeds with *any* model —
`llama3.1:8b` answers an embed probe with 4096 dimensions — so answering was never evidence
of belonging in the embedding list. `/api/show` reports real capabilities where `/api/tags`
does not, and `completion` / `embedding` decide. A model reporting neither is offered for
chat, the one thing every model can do.

**NVIDIA models are filtered to the ones that actually answer**, in two passes, because
entitlement and usability are different questions and only the first is free.

`/v1/models` lists NVIDIA's whole catalogue, but an account may call a fraction of it and the
rest answer `404 Function …: Not found for account` only once chosen. Entitlement is checked
before body validation, so posting an empty `messages` list separates them at no cost — 400
means reachable, 404 means not yours, neither runs inference.

Passing that says nothing about whether a model accepts the request this application sends.
Of the twenty that reach the second pass, several reject the output-cap field outright
(`extra_forbidden`), some are not chat models and answer 500, and some never answer at all.
So the survivors are asked for one token using the same field names the provider sends —
read from the provider class, so the probe cannot test a shape the provider no longer uses.

A **timeout is not cached** as a refusal: a large model can miss the deadline waking up and
answer comfortably once warm, and a remembered no would hide it until a restart. Definite
verdicts are cached on the class, so the first catalogue call after a restart pays for the
probing and later ones are a dict lookup.

What survives is what responds — not what is useful. A safety classifier answers
`{"User Safety": "safe"}` to anything, and that is a working endpoint by every mechanical
test; excluding it would mean judging a model by its name.

## Settings

| Setting | Does what |
| --- | --- |
| `GENERATION_BACKEND` | `anthropic` · `openai` · `google` · `cohere` · `nvidia` · `openrouter` · `ollama` |
| `EMBEDDING_BACKEND` | the same minus `anthropic`, which ships no embeddings API |
| `GENERATION_MODEL_ID` / `EMBEDDING_MODEL_ID` | named the way the vendor names them |
| `GENERATION_DEFAULT_MAX_TOKENS` | output cap. Sent as `max_tokens` to NVIDIA and `max_completion_tokens` to OpenAI |
| `GENERATION_DEFAULT_TEMPERATURE` / `GENERATION_THINKING` | defaults a notebook can override |
| `OLLAMA_HOST` / `OLLAMA_PORT` | where `ollama serve` is listening |
| `OLLAMA_CLOUD_BASE_URL` | a second Ollama over the network; its models appear alongside the local ones |
| `<VENDOR>_API_KEY` | only the key for a selected backend must be set; it is checked at startup |

## API

| Method | Path | |
| --- | --- | --- |
| `GET` | `/chat/models` | the full catalogue, probed |
| `GET` | `/chat/models/quick` | configured hosted models, no network calls |
| `PATCH` | `/chat/chats/{chat_id}/models` | point a notebook at different models |
| `PATCH` | `/chat/chats/{chat_id}/settings` | temperature, output length, chunking, colour |

## Tests

`tests/providers/` (catalogue, model ids, OpenRouter), `tests/chatting/test_interface.py`,
`test_provider_cache.py`, `test_nvidia_embedding.py`, `tests/contracts/test_structured_output.py`,
`test_schema_enforcement.py`, `tests/api/test_models.py`.

## Diagrams

[Providers: chat, embedding, reranking](../../software_engineering/4_class_diagrams/providers.png)

## Known limits

- Most of NVIDIA's catalogue is not callable from a given account; the picker shows what answers.
  See [deployment follow-ups](../roadmap/deployment_followups.md).
- A reasoning model asked for JSON can leave `content` empty; structured calls run with thinking off.
