"""The application's Prometheus metrics, and the one place they are defined.

Scraped from ``METRICS_PATH`` (default ``/metrics``). Counters and histograms
only — no gauge here runs a query at scrape time, because a scrape happens
every few seconds and a ``COUNT(*)`` on ``chunks`` is not something to do at
that cadence.

Two rules this module exists to enforce:

**Labels stay bounded.** Every distinct label value is a separate time series,
so ``chat_id``, ``asset_id`` and ``user_id`` must never appear on one. What is
safe is what comes from configuration rather than from data — ``provider``,
``model``, ``stage``, ``backend``, ``result`` — plus the *templated* route
(``/chat/chats/{chat_id}/documents``), which the instrumentator supplies.

**Buckets match the operation.** The defaults are tuned for web requests, in
milliseconds. Embedding and ingest here run in tens of seconds — a 461-chunk
document takes about a minute on the local 8B embedder — so those get their own
wide buckets. Left on the defaults every observation lands in ``+Inf`` and the
percentile panels read as flat lines.

Imported by controllers, providers and routes. It therefore imports nothing
from them, and nothing from ``utils`` beyond the settings it needs — ``utils``
sits underneath the whole application and a back-reference would be circular.
"""

from prometheus_client import REGISTRY, Counter, Histogram

# prometheus_client's default registry, shared with the HTTP instrumentator.
#
# A private CollectorRegistry() would be tidier in principle, but
# prometheus-fastapi-instrumentator 8.x silently drops its in-progress gauge
# when handed one — should_instrument_requests_inprogress is honoured only on
# the default registry. Sharing this one also brings in the stock process and
# GC collectors, which is a small bonus rather than a cost.


# Seconds. Web-request shaped: sub-millisecond to ten seconds.
_FAST = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

# Seconds. Model- and ingest-shaped: a second to ten minutes.
_SLOW = (0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)

# Counts of things per operation, not durations.
_COUNTS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500)

# Handed to the HTTP instrumentator in main.py. The stock set plus 30 and 60:
# an upload is a normal request here and runs for a minute, and without the
# long buckets every one of them lands in +Inf and the p99 panel goes flat.
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


# --- ingest -------------------------------------------------------------------

INGEST_DOCUMENTS = Counter(
    "ingest_documents_total",
    "Documents submitted for ingestion, by outcome.",
    # "duplicate" is a first-class outcome, not a failure: it is the dedupe
    # check doing its job, and it should be visible as its own line.
    ["result"],  # ok | duplicate | failed
    registry=REGISTRY,
)

INGEST_STAGE_SECONDS = Histogram(
    "ingest_stage_duration_seconds",
    "Time spent in each stage of ingesting one document.",
    # The same vocabulary the UI progress bar uses — see routes/chat/_helpers.
    ["stage"],  # extracting | chunking | storing | indexing
    buckets=_SLOW,
    registry=REGISTRY,
)

INGEST_CHUNKS = Histogram(
    "ingest_chunks_per_document",
    "Chunks produced from one document.",
    buckets=_COUNTS,
    registry=REGISTRY,
)


# --- embedding ----------------------------------------------------------------

EMBEDDING_REQUESTS = Counter(
    "embedding_requests_total",
    "Calls to the embedding model, by outcome.",
    ["provider", "model", "result"],  # result: ok | error
    registry=REGISTRY,
)

EMBEDDING_SECONDS = Histogram(
    "embedding_duration_seconds",
    "Wall time of one embedding call, however many texts it carried.",
    ["provider", "model"],
    buckets=_SLOW,
    registry=REGISTRY,
)

EMBEDDING_TEXTS = Histogram(
    "embedding_texts_per_request",
    "Texts sent in one embedding call — the effective batch size.",
    buckets=_COUNTS,
    registry=REGISTRY,
)


# --- generation ---------------------------------------------------------------

GENERATION_REQUESTS = Counter(
    "generation_requests_total",
    "Calls to the chat model, by outcome.",
    ["provider", "model", "result"],  # result: ok | error
    registry=REGISTRY,
)

GENERATION_SECONDS = Histogram(
    "generation_duration_seconds",
    "Wall time of one generation, start to last token.",
    ["provider", "model"],
    buckets=_SLOW,
    registry=REGISTRY,
)

GENERATION_TTFT_SECONDS = Histogram(
    "generation_time_to_first_token_seconds",
    "Time from request to the first token reaching the client.",
    # The number that tracks how fast an answer *feels*. Total duration does
    # not: a long answer that starts instantly reads as fast, and a short one
    # that stalls for ten seconds reads as broken.
    ["provider", "model"],
    buckets=_FAST,
    registry=REGISTRY,
)

GENERATION_TOKENS = Counter(
    "generation_tokens_total",
    "Tokens reported by the provider, in and out.",
    # Already collected by LLMChattingInterface._log_usage, but only at DEBUG,
    # so at the default LOG_LEVEL this data never left the process.
    ["provider", "model", "direction"],  # direction: input | output
    registry=REGISTRY,
)


# --- retrieval ----------------------------------------------------------------

RETRIEVAL_SECONDS = Histogram(
    "retrieval_duration_seconds",
    "Vector search time for one question.",
    buckets=_FAST,
    registry=REGISTRY,
)

RETRIEVAL_HITS = Histogram(
    "retrieval_hits",
    "Passages returned by one search.",
    buckets=(0, 1, 2, 3, 5, 8, 13, 21),
    registry=REGISTRY,
)

ANSWERS = Counter(
    "answers_total",
    "Answers produced, split by whether documents backed them.",
    # The ratio to watch: a notebook with sources answering ungrounded means
    # retrieval found nothing, which is a silent failure today.
    ["grounded"],  # true | false
    registry=REGISTRY,
)


# --- vector store -------------------------------------------------------------

VECTOR_UPSERT_SECONDS = Histogram(
    "vector_upsert_duration_seconds",
    "Time to write one batch of vectors.",
    ["backend"],  # postgres | qdrant
    buckets=_SLOW,
    registry=REGISTRY,
)


__all__ = [
    "REGISTRY",
    "HTTP_BUCKETS",
    "INGEST_DOCUMENTS",
    "INGEST_STAGE_SECONDS",
    "INGEST_CHUNKS",
    "EMBEDDING_REQUESTS",
    "EMBEDDING_SECONDS",
    "EMBEDDING_TEXTS",
    "GENERATION_REQUESTS",
    "GENERATION_SECONDS",
    "GENERATION_TTFT_SECONDS",
    "GENERATION_TOKENS",
    "RETRIEVAL_SECONDS",
    "RETRIEVAL_HITS",
    "ANSWERS",
    "VECTOR_UPSERT_SECONDS",
]
