# Reranking

## What it does

Re-scores the vector hits with a cross-encoder before they reach the prompt, so the passages the
model reads are the most relevant ones, not merely the nearest in embedding space. Works for
Arabic and English alike.

## How it works

- `src/application/providers/reranking/`: `RerankerInterface`, `LlamaCppRerankerProvider`,
  `TEIRerankerProvider` and a factory. With `RERANKER_ENABLED` off the factory builds a
  pass-through and retrieval behaves exactly as before.
- Vector search fetches `RERANK_CANDIDATES` hits per query. `ChatService._rerank` scores them
  against the standalone question and keeps the best `top_k`, dropping anything under
  `RERANK_MIN_SCORE`.
- The model is `BAAI/bge-reranker-v2-m3` as a quantised GGUF, served by llama.cpp's
  `llama-server` in the `reranker` container, about 1 GB of RAM. `RERANKER_PROVIDER=tei` targets
  a Hugging Face text-embeddings-inference server instead, which is what ran before.
- **A failed or slow rerank falls back to vector order.** A rerank that passes
  `RERANKER_TIMEOUT_S` is abandoned: a slow reranker must not stall the answer.
- Picture hits carry their caption as text, so passages and captions are scored on one scale.

## Settings

| Setting | Does what |
| --- | --- |
| `RERANKER_ENABLED` | off = pass-through |
| `RERANKER_PROVIDER` / `RERANKER_URL` / `RERANKER_MODEL_ID` | `llamacpp` or `tei`, where it listens, and the model name |
| `RERANKER_TIMEOUT_S` | after this the hits keep their vector order |
| `RERANK_CANDIDATES` | vector hits fetched per query; costs reranker time roughly linearly |
| `RERANK_MIN_SCORE` | relevance floor, 0..1 |

## Tests

`tests/chatting/test_rerank.py`.

## Diagrams

[Providers: chat, embedding, reranking](../../software_engineering/4_class_diagrams/providers.png) · [where it sits in an answer](../../software_engineering/3_sequence_diagrams/chat_agentic_rag.png)

## Known limits

On CPU the reranker costs about 50 to 150 ms per passage.
