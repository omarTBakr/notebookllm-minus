from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enums import (
    CeleryTaskFunction,
    DbBackend,
    DistanceMethod,
    IndexType,
    Language,
    LLMChattingProvider,
    LLMEmbeddingProvider,
    LogFormat,
    LogLevel,
    PdfLoader,
    ThinkingLevel,
    TruncateMode,
)

from .resources import cpu_count

SRC_DIR = Path(__file__).parent.parent.parent  # shared/utils/config.py -> src/

# NVIDIA's hosted NIM endpoint. Here rather than inside NvidiaChatProvider so
# that pointing the app at a self-hosted NIM is a .env line, and so the URL
# appears once in the codebase.
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

# OpenRouter's API, for the same reason: one line in .env repoints it at a
# proxy or a self-hosted gateway, and the URL appears once in the codebase.
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# LOG_LEVEL is the one enum-typed field whose canonical spelling is upper
# case (it mirrors `logging`'s own level names), so it gets its own
# validator below instead of joining this set.
_UPPERCASE_FIELDS = (LogLevel,)


def _enum_choice_fields(annotations: dict, *, exclude: tuple = ()) -> tuple[str, ...]:
    """Names of the fields, from a class body's raw annotations, whose type
    is a `str` `Enum` — every field pydantic will reject on a spelling
    difference rather than a wrong value.

    Derived instead of listed by hand: the alternative is a tuple of field
    names that silently goes stale the moment someone adds or renames an
    enum-typed setting, since nothing would force it to be updated alongside
    the field itself.
    """
    return tuple(
        name
        for name, annotation in annotations.items()
        if isinstance(annotation, type) and issubclass(annotation, Enum) and annotation not in exclude
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SRC_DIR / ".env",  # resolves to src/.env regardless of CWD
        case_sensitive=False,
        extra="ignore",
        # Store the enum's *value*, so every reader still sees a plain string:
        # these end up in log config, JSON responses and f-strings, where an
        # enum member would render as "LogLevel.INFO" rather than "INFO".
        use_enum_values=True,
        # ...including the defaults below, which pydantic would otherwise leave
        # as enum members, making a field's type depend on whether it was set.
        validate_default=True,
    )

    # --- application ---------------------------------------------------------
    APPLICATION_NAME: str
    APP_VERSION: str

    # --- uploads -------------------------------------------------------------
    ALLOWED_TYPES: list
    MAX_FILE_SIZE: int = 10485760  # hard ceiling on a single upload (bytes)
    MAX_FILE_CHUNK_SIZE: int  # streaming read size while uploading

    # --- link sources --------------------------------------------------------
    # A source added from a URL: a PDF, an article, or a YouTube transcript.
    # Downloads are capped at MAX_FILE_SIZE, like an upload.
    # Seconds allowed for the whole fetch -- connect, redirects and body. It
    # runs inside the request, so the user waits this long at most for an
    # answer about the link.
    URL_FETCH_TIMEOUT: int = 30
    # Transcript languages to prefer, in order, for a YouTube video. A manual
    # transcript in one of these wins, then an auto-generated one, then any.
    YOUTUBE_TRANSCRIPT_LANGUAGES: list[str] = ["ar", "en"]
    # YouTube blocks most cloud IPs from fetching transcripts. An http(s)
    # proxy here (e.g. a residential one) is how the server gets around that;
    # empty fetches directly, which works from a home connection.
    YOUTUBE_PROXY_URL: str = ""

    # --- Arabic OCR ----------------------------------------------------------
    # Two things read a damaged page today: the correction pass below (pages
    # whose text is there but badly spaced) and OCR_UNREADABLE_PAGES (pages
    # with no usable text at all). The per-page OCR re-read of badly spaced
    # Arabic (OCR_ENABLED / OCR_EXTRACTOR / OCR_MIN_CHARS) was removed: batched
    # ingest never reached it. docs/roadmap/arabic_reread_in_batched_ingest.md
    # records what it did and what bringing it back would take.
    #
    # qalam's word-break threshold, as a fraction of the type size: a pen
    # move wider than this between two glyphs becomes a space. Upstream
    # hard-codes 0.25, which fuses whole lines on this corpus; 0.15 spaces
    # them correctly. Only the patched build (qalam 0.1.1+wordgap) reads it --
    # stock qalam ignores it silently. See QalamExtractor._export_word_gap.
    QALAM_WORD_GAP: float = 0.15
    # OCR the pages qalam says have no usable text layer -- a scan, a title
    # page drawn as a picture, glyphs that decode to nothing -- with
    # tesseract-best (Arabic + English). Without it those pages index as
    # empty. Warns and skips if qalam or tessdata_best is missing.
    OCR_UNREADABLE_PAGES: bool = True
    # Where ara.traineddata from tessdata_best lives. The distribution package
    # ships the *fast* model, which is a different model and three times worse
    # on this corpus; the image downloads the better one to this path.
    TESSDATA_BEST: str = "/usr/share/tessdata-best"
    # --- hosted OCR (optional) -----------------------------------------------
    # OpenRouter fronts many models behind one OpenAI-compatible API, so the
    # model is configuration rather than code. The key is shared: it enables the
    # `openrouter` chat/embedding backend (with its models in the picker) and
    # the `openrouter` OCR extractor. OPENROUTER_MODEL belongs to the extractor,
    # which is a benchmark candidate and not on the ingestion path: pages leave
    # the machine, and the rate limit belongs to someone else.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "minimax/minimax-m3:free"
    # How many pages are OCR'd at once. 0 means "every CPU this process may
    # use" — cgroup quota and affinity included, see PdfLayoutService.
    #
    # Threads, not processes: a Celery prefork worker is daemonic and may not
    # fork, and it does not need to. pytesseract runs the `tesseract` binary as
    # a subprocess, so the GIL is released for the whole of the work and page
    # level parallelism scales close to linearly.
    #
    # Each concurrent page holds a 300-dpi RGB raster — roughly 26 MB for A4 —
    # so this is also the knob for OCR's peak memory. Lower it before lowering
    # the DPI, which costs accuracy.
    OCR_WORKERS: int = 0

    # --- LLM post-processing of the extracted text ---------------------------
    # PyMuPDF loses no glyphs but a PDF's text layer is a drawing instruction
    # list, not a document: words arrive split where a font changed, fused where
    # no space was emitted, hyphenated across line breaks. The text is nearly
    # right, which is exactly what makes it unsearchable. This pass hands each
    # page to a local model to undo that damage — see src/application/ocr_prompts/en/ocr_correction.py
    # for what it is and is not allowed to change.
    #
    # It replaced a tesseract re-read of the same pages: OCR throws away the
    # per-word boxes a citation highlight is drawn from and costs seconds a
    # page to render.
    POSTPROCESS_ENABLED: bool = True
    # Qualified (`<source>/<tag>`), so ProviderCache resolves which host serves
    # it without this module knowing about a vendor at all. `local/` is the
    # machine's own Ollama daemon, `nvidia/` a hosted NIM.
    #
    # Measured on a page of split-word damage, scored against a hand-written
    # ideal correction:
    #
    #   nvidia/nemotron-3-super-120b-a12b   10.6s   fidelity 1.000
    #   nvidia/meta/llama-3.2-11b-vision    18.6s   fidelity 1.000, but
    #                                               unusable on Arabic: it
    #                                               returns an empty `text`
    #                                               field and fails all three
    #                                               structured-output attempts
    #   local/gemma4:e4b                    29.6s   repairs English, no-op on
    #                                               Arabic
    #
    # gemma4:e4b is 10 GB against 8 GB of VRAM on this machine, so Ollama can
    # only offload ~34% of its layers and the rest runs on CPU -- `num_gpu=99`
    # is refused outright with "memory layout cannot be allocated". That is why
    # the local model was slow and why nvidia-smi showed the GPU near idle: the
    # CPU half was the bottleneck, not the GPU.
    #
    # A NIM id keeps its publisher, so the qualified form has two slashes:
    # source `nvidia`, tag `nvidia/nemotron-3-super-120b-a12b`.
    POSTPROCESS_MODEL_ID: str = "nvidia/nvidia/nemotron-3-super-120b-a12b"
    # Pages per batch — one Celery message, one unit of work. The ceiling is a
    # payload and a failure-blast-radius decision, not a model one: a batch is
    # retried whole, and its parsed pages (text *and* word boxes) travel through
    # the result backend.
    PDF_BATCH_PAGES: int = 5
    # Pages per *model call*. Not the same knob as the one above: that one
    # sizes a queue message, this one sizes a prompt. At 5 -- matching
    # PDF_BATCH_PAGES above -- it is one call per batch.
    #
    # Measured on ten *dense* pages (~645 tokens each, real page density rather
    # than one line), scored against a hand-written ideal:
    #
    #   10 pages/call, max_tokens 16384:  217.4s  fidelity 0.998
    #   10 pages/call, max_tokens 32768:  121.6s  fidelity 1.000
    #    5 pages/call, max_tokens 16384:   52.2s  fidelity 1.000
    #
    # Two things to take from that. The token budget is not optional -- ten
    # dense pages need ~6.5k of output and a budget that cannot hold them
    # degrades the answer rather than failing it, which is the quiet kind of
    # wrong. And packing is not free: 5 per call was 2.3x faster at identical
    # fidelity, because the calls overlap and one big call cannot.
    #
    # 5, not 10: a 10-page Arabic call measured 425s, and the length guard
    # rejected a page for coming back at 34% of its length -- the model
    # thinning out toward the end of a long call. Raise it only with a
    # measurement on the corpus in front of you.
    POSTPROCESS_PAGES_PER_CALL: int = 5
    # Concurrent model calls per batch.
    #
    # 4 suits a hosted endpoint, where the calls really do overlap and the only
    # ceiling is the vendor's rate limit. Against a *local* Ollama this should
    # be 2: it serialises requests per model, so extra callers queue inside the
    # server while each still holds its pages in memory here.
    POSTPROCESS_CONCURRENCY: int = 4
    # Summarise every chunk of a new document as soon as it is stored, one
    # batch of ten per message on the correction queue, with the notebook's own
    # generation model. These are the summaries Studio generates from; with
    # them in place a new notebook's first flashcards skip the summarising pass.
    # Off, Studio still summarises on first use -- nothing depends on this.
    INGEST_SUMMARISE: bool = True
    # Context window for the correction call, in tokens. Ollama only -- a
    # hosted model sizes its own, and the factory passes this no further than
    # the Ollama branch.
    #
    # It has to be set explicitly there: Ollama otherwise applies its own
    # default (commonly 4096) and silently truncates, which here means a page
    # that comes back with its tail missing and no error anywhere. A page of
    # Arabic runs 1-3k tokens and the answer repeats it, so the floor is
    # roughly 4x a page -- times POSTPROCESS_PAGES_PER_CALL.
    POSTPROCESS_NUM_CTX: int = 16384
    # Studio (mind map, flashcards, quiz) runs the notebook's chat model with
    # its thinking on, for the quality of the items. A reasoning model thinks
    # before it answers, so the window has to hold the prompt, the thinking and
    # the JSON. Measured on gemma4:e4b with ten real chunks: Ollama's default
    # window (4096) stopped it mid-thought every time (done_reason='length',
    # no answer, every set failed); 16384 returned all ten summaries in 45 s
    # (~2.3k prompt tokens, ~2.4k out, 7k characters of it thinking).
    STUDIO_NUM_CTX: int = 16384
    # The answering clients' window (the answer, the planner, each sub-answer).
    # Ollama's default (4096) was enough for plain chat, not for a grounded
    # prompt: a dozen passages, the instructions and the history. An Arabic
    # question's sub-answers filled it with the prompt alone and came back
    # empty (done_reason='length'), so the answer said the documents did not
    # cover a question they answered. Same as STUDIO_NUM_CTX on purpose: Ollama
    # reloads a model whenever the window it is asked for changes, so one size
    # for chat and Studio stops it reloading between them. Ollama only.
    CHAT_NUM_CTX: int = 16384
    # Output cap for one Studio call, thinking included. Five times what the
    # measured call used: a cap for a runaway, not a budget an answer needs.
    # BATCH_ATTEMPT_TIMEOUT in ArtifactService still bounds each attempt.
    STUDIO_MAX_TOKENS: int = 12288
    # Output cap for one correction, which has to cover
    # POSTPROCESS_PAGES_PER_CALL whole pages: a correct answer reproduces every
    # page it was given, in full. Ten dense pages is ~6.5k tokens of output
    # before the JSON frame.
    #
    # Five dense pages is ~5.5k tokens of output, so 12288 is roughly double
    # what a correct answer needs -- and that headroom is the point of the
    # number rather than an accident.
    #
    # It came down from 32768, which was not generosity but a budget for a
    # runaway: asked to reproduce an Arabic page, the model fell into a
    # repetition loop and emitted 286,000 characters of "remained remained
    # remained" before hitting the cap -- one batch, 810 seconds, three
    # attempts, no answer. A cap near the honest answer size turns that from
    # minutes of wasted generation into a fast failure the retry can follow.
    POSTPROCESS_MAX_TOKENS: int = 12288
    # A correction is only accepted if its length is within this factor of the
    # original, either way. The failure this catches is the model summarising a
    # page or answering it instead of reproducing it — both of which return
    # fluent, plausible, much shorter text that no schema can reject. Outside
    # the band the page keeps its extracted text.
    POSTPROCESS_LENGTH_TOLERANCE: float = 0.5

    # --- chunking ------------------------------------------------------------
    # A chunk shorter than this is not a retrieval unit, it is debris. The
    # recursive splitter flushes whatever short splits it has accumulated as
    # soon as the next split is big enough to need recursion, so a running
    # header or a page number sitting on its own line before the body becomes a
    # standalone chunk. Measured on the 274-page Arabic book, OCR on and
    # chunk_size 1000: 110 of 803 chunks under 50 characters, 78 under 10 —
    # "سورية", "0/", "\u0661". The same book with OCR off is 742 chunks and
    # only 15 under 100, because OCR recovers the running header on pages whose
    # text layer had lost it — that is, the better the extraction, the more
    # debris this produces. They embed to
    # nothing in particular, and because there are so many of them one usually
    # still lands in the top 5, which is how a grounded answer ends up citing
    # five single words.
    #
    # 100 is above the debris (the largest orphan measured was 89) and well
    # below a real paragraph, so nothing with content in it is touched.
    MIN_CHUNK_CHARS: int = 100

    # --- indexing ------------------------------------------------------------
    # How many chunks are embedded per request to the model. One batch is one
    # round trip; peak memory is one batch of text plus one batch of vectors.
    CHUNKING_BATCH_SIZE: int = 512

    # --- document database ---------------------------------------------------
    # Which provider backs the main data store. Postgres also holds the vectors;
    # Mongo pairs with Qdrant. See the README's "Database backends".
    DOCUMENT_DB_BACKEND: DbBackend = DbBackend.MONGO

    # mongo — only read when DOCUMENT_DB_BACKEND is "mongo"
    MONGO_HOST: str = "localhost"
    MONGO_PORT: int = 27017
    MONGO_USER: str = "root"
    MONGO_PASSWORD: str = "example"
    MONGO_DB_NAME: str = "notebookllm"

    # postgres — only read when DOCUMENT_DB_BACKEND is "postgres". The DSN is
    # assembled from these by postgres_url / postgres_async_url below.
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "[PASSWORD]"
    POSTGRES_DB: str = "notebookllm-minus"

    # --- vector database -----------------------------------------------------
    # VECTOR_DB_PATH: str = "assets/qdrant_db"  # embedded mode; relative resolves against src/
    VECTOR_DB_HOST: str | None = None  # set to use a server instead of VECTOR_DB_PATH
    VECTOR_DB_PORT: int | None = None
    VECTOR_DB_API_KEY: str | None = None
    # Fixed when a collection is created; changing it means rebuilding.
    VECTOR_DB_DISTANCE_METHOD: DistanceMethod = DistanceMethod.COSINE
    # ANN algorithm VectorRepository.create_index() builds by default. IVFFLAT
    # is pgvector-only; Qdrant rejects it (Qdrant only builds HNSW).
    VECTOR_DB_INDEX_TYPE: IndexType = IndexType.HNSW

    # --- llm providers -------------------------------------------------------
    # Deliberately different types: Anthropic ships no embeddings API, so
    # "anthropic" is valid for generation and rejected for embedding.
    GENERATION_BACKEND: LLMChattingProvider
    EMBEDDING_BACKEND: LLMEmbeddingProvider

    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL_ID: str = "claude-sonnet-4-20250514"
    # Required only for an identity-linked API key, which Anthropic rejects
    # outright without it: "anthropic-workspace-id is required when
    # authenticating with an identity-linked API key". An ordinary key needs
    # no workspace, so this stays optional and the header is omitted when
    # unset rather than sent empty. Found in the Anthropic console under
    # Settings -> Workspaces; the id looks like "wrkspc_...".
    ANTHROPIC_WORKSPACE_ID: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE_URL: str | None = None  # for OpenAI-compatible endpoints
    GOOGLE_API_KEY: str | None = None
    GOOGLE_MODEL_ID: str = "gemini-3.6-flash"
    COHERE_API_KEY: str | None = None
    # NVIDIA NIM — an OpenAI-compatible endpoint with its own key, kept
    # separate from OPENAI_API_KEY / OPENAI_API_BASE_URL so both vendors can be
    # configured at once. One key serves chat and embeddings.
    NVIDIA_API_KEY: str | None = None
    # Unlike OPENAI_API_BASE_URL, this has a real default rather than meaning
    # "the SDK's own": an OpenAI client with an nvapi key and no endpoint would
    # dial api.openai.com and fail with an authentication error that says
    # nothing about the mistake. Blank is read as unset (validator below).
    NVIDIA_API_BASE_URL: str = NVIDIA_DEFAULT_BASE_URL
    # NVIDIA's per-request input cap. Below CHUNKING_BATCH_SIZE's default of
    # 512, so NvidiaEmbeddingProvider splits a batch rather than failing one:
    # 257 inputs answers "input count 257 exceeds maximum allowed batch size".
    # A setting because it is NVIDIA's number, not ours, and they can raise it.
    NVIDIA_EMBEDDING_MAX_BATCH: int = 256
    # What to do with a text longer than the model's context. END means one
    # over-long chunk costs its own tail rather than failing the whole upload;
    # NONE is the vendor default and fails the request instead.
    NVIDIA_EMBEDDING_TRUNCATE: TruncateMode = TruncateMode.END
    # OpenRouter's endpoint. The key is OPENROUTER_API_KEY above: one key serves
    # the `openrouter` chat/embedding backend and the OCR extractor. Kept apart
    # from OPENAI_API_BASE_URL so both vendors can be configured at once; blank
    # is read as unset (validator below).
    OPENROUTER_API_BASE_URL: str = OPENROUTER_DEFAULT_BASE_URL
    # Ollama is local and takes no key — a reachable host is the whole config.
    OLLAMA_HOST: str = "localhost"
    OLLAMA_PORT: int = 11434
    # A second Ollama, reached over the network rather than on this machine —
    # the "web" models in the picker. Optional: unset means local-only, which
    # is the ordinary setup and behaves exactly as before.
    OLLAMA_CLOUD_BASE_URL: str | None = None

    # Not a .env knob — never documented in .env.example. ProviderCache sets
    # this via model_copy() to hand a per-model client a resolved host (local
    # port vs. the cloud URL) without either factory learning there are two
    # hosts at all. See ollama_base_url below and factories/provider_cache.py.
    OLLAMA_BASE_URL_OVERRIDE: str | None = None

    # Also not a .env knob, and set the same way: ProviderCache injects it when
    # a caller asks for a specific context window. None means "Ollama's own
    # default", which is correct for chat and wrong for anything that sends a
    # whole document page — see OllamaChatProvider.num_ctx for why the
    # difference is invisible rather than an error.
    OLLAMA_NUM_CTX: int | None = None

    # Also not a .env knob. None means "say nothing", which leaves a model's own
    # default alone and is right for chat; False turns a reasoning model's
    # scratchpad off. ProviderCache injects it for callers that need a plain
    # answer rather than a monologue -- see OpenAIChatProvider.thinking.
    # OpenAI-compatible providers only: an Ollama model ignores it and follows
    # GENERATION_THINKING (see there).
    CHAT_THINKING_OVERRIDE: bool | None = None

    # --- generation ----------------------------------------------------------
    GENERATION_MODEL_ID: str
    # Generous on purpose: a reasoning model spends this budget on its
    # scratchpad *before* the answer, so a small cap can be consumed entirely
    # by thinking and leave the reply truncated or empty.
    GENERATION_DEFAULT_MAX_TOKENS: int = 4096
    GENERATION_DEFAULT_TEMPERATURE: float = 0.1
    # Ask the model to expose its reasoning. Ollama-only; ignored elsewhere,
    # and dropped automatically if the model doesn't support it.
    #
    # On Ollama this is the only thinking switch, and it can only turn thinking
    # *on*: true or a level sends `think`, while false sends nothing, which
    # leaves the model's own default -- and a reasoning model such as
    # gemma4:e4b thinks by default. The per-call thinking=False that ProviderCache
    # accepts (CHAT_THINKING_OVERRIDE below) never reaches an Ollama model. So
    # every token cap on a local reasoning model must leave room for thinking.
    # Kept this way deliberately: thinking is on for answer quality.
    GENERATION_THINKING: ThinkingLevel = ThinkingLevel.TRUE

    # --- embedding -----------------------------------------------------------
    EMBEDDING_MODEL_ID: str
    EMBEDDING_MODEL_SIZE: int  # must match the embedding model, see .env.example

    # --- chat ----------------------------------------------------------------
    DEFAULT_LANG: Language = Language.EN  # prompt locale when a chat names none
    CHAT_HISTORY_LIMIT: int = 10  # prior turns sent as context
    RETRIEVAL_TOP_K: int = 5  # chunks retrieved per grounded answer
    # Passages scoring below this are dropped before they reach the prompt.
    # Scores are similarities on both backends (higher is better), so 0.0 is
    # "keep everything" and is the default: a floor that is too high silently
    # ungrounds answers, which is worse than a weak citation. Raise it once
    # you have looked at real scores for your corpus and model.
    RETRIEVAL_MIN_SCORE: float = 0.0

    # --- agentic answering ---------------------------------------------------
    # Before retrieving, a planner call classifies the question (chitchat,
    # simple, complex), rewrites it to stand alone without the chat history,
    # and lists retrieval queries. A complex question is answered part by part
    # (retrieve, rerank, a short cited sub-answer each, in parallel), then one
    # streamed call writes the final answer from those parts and the original
    # question. Off restores single-pass retrieval on the raw question.
    AGENTIC_RAG_ENABLED: bool = True
    # Parts a complex question may be split into: each one is a retrieval, a
    # rerank and a model call, all concurrent.
    AGENTIC_MAX_SUB_QUERIES: int = 3
    # A sub-answer is a few cited sentences, but the cap covers the model's
    # thinking too, and a local model thinks whatever the code asks (see
    # GENERATION_THINKING). At 800, gemma4:e4b spent the whole budget thinking
    # over a part's Arabic passages and returned no text (done_reason='length');
    # the part was reported as not covered and the answer said the documents
    # did not cover a question they answered. Same as
    # GENERATION_DEFAULT_MAX_TOKENS, for the same reason.
    AGENTIC_SUBANSWER_MAX_TOKENS: int = 4096

    # --- guardrails ----------------------------------------------------------
    # Model-based checks before the answer model sees a question (input) and
    # on the answer as it streams (output). Each stage has its own model and
    # its own Celery queue and worker, and requests are judged in micro-batches:
    # a batch goes as soon as GUARDRAIL_BATCH_SIZE requests are waiting OR
    # GUARDRAIL_BATCH_MAX_WAIT_S has passed, whichever is first, so a lone
    # request never waits for the batch to fill.
    GUARDRAILS_ENABLED: bool = False
    # Any provider-qualified id ProviderCache understands ("local/qwen3:4b",
    # "nvidia/meta/llama-3.1-8b-instruct"). None uses GENERATION_MODEL_ID.
    # Thinking is always off for these: they answer in JSON.
    GUARDRAIL_INPUT_MODEL: str | None = None
    GUARDRAIL_OUTPUT_MODEL: str | None = None
    GUARDRAIL_BATCH_SIZE: int = 8
    GUARDRAIL_BATCH_MAX_WAIT_S: float = 0.25
    # How long the API waits for a verdict before applying GUARDRAIL_FAIL_MODE.
    GUARDRAIL_TIMEOUT_S: float = 20.0
    # open: a check that cannot complete (worker down, timeout, unparseable
    # verdict) lets the text through, logged and counted. closed: it refuses.
    GUARDRAIL_FAIL_MODE: Literal["open", "closed"] = "open"
    # The answer is judged in chunks of at least this many characters, cut at
    # a sentence end. Smaller is safer and costs more checks.
    GUARDRAIL_OUTPUT_CHUNK_CHARS: int = 400

    # --- reranking -----------------------------------------------------------
    # A cross-encoder re-scores the vector hits before the best top_k reach the
    # prompt. Served by the `reranker` container: llama.cpp's llama-server with
    # a Q8_0 GGUF of BAAI/bge-reranker-v2-m3 (multilingual, so Arabic passages
    # score properly; ~1 GB RAM). RERANKER_PROVIDER=tei targets a Hugging Face
    # text-embeddings-inference server instead.
    # Off builds a pass-through, and retrieval behaves exactly as before.
    RERANKER_ENABLED: bool = False
    RERANKER_PROVIDER: str = "llamacpp"
    RERANKER_URL: str = "http://reranker:8080"
    RERANKER_MODEL_ID: str = "BAAI/bge-reranker-v2-m3"
    # A rerank that takes longer than this is abandoned and the hits keep
    # their vector order: a slow reranker must not stall the answer.
    RERANKER_TIMEOUT_S: float = 10.0
    # How many vector hits each query fetches for the reranker to choose from.
    # Wider recalls more and costs reranker time roughly linearly (on CPU,
    # about 50-150 ms per passage for bge-reranker-v2-m3).
    RERANK_CANDIDATES: int = 20
    # Reranked passages below this relevance (0..1) are dropped. 0.0 keeps
    # everything, for the same reason RETRIEVAL_MIN_SCORE defaults to it.
    RERANK_MIN_SCORE: float = 0.0

    # --- images (multimodal retrieval) ---------------------------------------
    # Figures inside PDFs, image uploads and an image attached to a question
    # become searchable. One multimodal embedder (EmbeddingGemma 2: text and
    # images in one 768-d space, Arabic included) fills a second collection
    # per notebook, `project_<id>__img`, beside the text one. It is global,
    # not per chat, so a chat's own text embedder choice never touches it.
    # Off leaves the pipeline exactly as it was: no extraction, no captions,
    # no image search.
    MULTIMODAL_ENABLED: bool = False
    # "ollama" (the host daemon at OLLAMA_HOST) or "llamacpp" (a llama-server
    # with an mmproj, at IMAGE_EMBEDDING_URL).
    IMAGE_EMBEDDING_BACKEND: str = "ollama"
    IMAGE_EMBEDDING_MODEL_ID: str = "embeddinggemma-2:440m-q8_0-text-vision"
    IMAGE_EMBEDDING_SIZE: int = 768
    IMAGE_EMBEDDING_URL: str = "http://embedder:8080"
    IMAGE_EMBEDDING_TIMEOUT_S: float = 60.0
    # A vision chat model writes a short caption for every image at ingest.
    # The caption is what the reranker scores and what a model without vision
    # reads. Empty skips the model and uses the nearest text on the page
    # ("Figure 3: ...") instead. Any id the model picker accepts works here.
    IMAGE_CAPTION_MODEL_ID: str = "local/gemma3:4b"
    # Embedded pictures smaller than this (either side, in pixels) or covering
    # less than IMAGE_MIN_AREA_RATIO of the page are icons and bullets, not
    # figures. One that repeats on more than IMAGE_MAX_REPEAT pages of the
    # same document is a logo or a header band.
    IMAGE_MIN_SIDE_PX: int = 96
    IMAGE_MIN_AREA_RATIO: float = 0.02
    IMAGE_MAX_REPEAT: int = 3
    # Longest side an image is scaled down to before it is embedded, captioned
    # or shown to a model. Bigger costs tokens and VRAM, not accuracy.
    IMAGE_MAX_SIDE_PX: int = 1024
    # Image hits each query keeps from the image collection, and the vector
    # similarity floor they need first.
    IMAGE_TOP_K: int = 4
    IMAGE_MIN_SCORE: float = 0.0
    # At most this many images reach a vision model with one answer.
    IMAGE_MAX_PER_ANSWER: int = 4
    # Largest image accepted from the chat composer, in bytes.
    IMAGE_QUERY_MAX_BYTES: int = 5 * 1024 * 1024
    # Figure <-> passage links. At ingest each picture is linked to the text
    # chunks that refer to it ("Figure 3", "الشكل ٣") or sit next to it on the
    # page; at answer time a retrieved passage brings its linked picture and a
    # retrieved picture brings the passage that explains it. The partners are
    # added after the ranked hits, never in place of one, and capped per
    # answer by the last two settings.
    LINKS_ENABLED: bool = True
    LINK_MAX_TEXT_PER_IMAGE: int = 2
    LINK_MAX_IMAGES_PER_TEXT: int = 2
    LINK_MAX_IMAGES_PER_ANSWER: int = 2
    LINK_MAX_PASSAGES_PER_ANSWER: int = 2

    # Which library extracts text from PDFs. Measured on the 274-page Arabic
    # guide (25 MB), whole document, after NFKC normalisation:
    #
    #   pypdf        29s    8-9/14 real words   90,874 lost glyphs
    #   pdfplumber   52s    0/14   real words   92,514 lost glyphs
    #   pymupdf     543s   11/14   real words        0 lost glyphs
    #
    # pdfplumber emits right-to-left text in *visual* order — the characters
    # come out reversed, so not one real Arabic word survives and retrieval on
    # an Arabic corpus collapses to nothing. It is kept here only because it
    # is markedly better at Latin tables, which the other two flatten.
    #
    # pymupdf is the default: it is the only extractor that loses no glyphs at
    # all, it recovers words the other two drop, and — the reason this class
    # of setting exists at all — it is the only one that captures the word
    # coordinates a citation's highlight is drawn from (PdfLayoutService).
    # Neither pypdf nor pdfplumber can produce a highlight regardless of this
    # setting; there is no faster way to get one.
    #
    # The cost is real and does not move with the embedding model: it is
    # entirely in pymupdf's own word-by-word PDF parsing (PdfLayoutService),
    # which runs to completion *before* embedding starts and does not touch
    # it — switching EMBEDDING_MODEL_ID to something smaller changes how long
    # embedding takes, not this. Parallelised across a process pool (pages
    # don't depend on one another), which brought the document above from
    # ~520s serial to ~79s on a 24-core machine — real, but short of a 24x
    # speedup; a handful of dense pages set a floor no amount of splitting
    # moves. Fewer cores buys less of a discount, and a container capped to
    # 1-2 CPUs sees close to the full serial cost. Set to PYPDF to trade the
    # highlight away for the old, faster extraction.
    PDF_LOADER: PdfLoader = PdfLoader.PYMUPDF

    # --- logging -------------------------------------------------------------
    LOG_LEVEL: LogLevel = LogLevel.INFO
    LOG_FORMAT: LogFormat = LogFormat.TEXT
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True
    LOG_DIR: str = "logs"  # relative paths resolve against src/
    LOG_FILE_NAME: str = "notebookllm-minus.log"
    LOG_MAX_BYTES: int = 10485760  # 10 MB per file before rotating
    LOG_BACKUP_COUNT: int = 5

    # --- metrics -------------------------------------------------------------
    # Prometheus scrapes METRICS_PATH. Off switches the endpoint off entirely;
    # the collectors stay defined but nothing is exposed.
    METRICS_ENABLED: bool = True
    METRICS_PATH: str = "/metrics"
    # Where each Celery worker serves its own metrics. Ingest, embedding and
    # LLM generation run in the workers, not the API, so without this port the
    # histograms they record never reach Prometheus. See celery_app.py.
    WORKER_METRICS_PORT: int = 9808

    # --- accounts -------------------------------------------------------------
    # Email + password signup/login in front of the whole app. A signed-in
    # browser holds a random token in an HttpOnly cookie; only its sha256 is
    # stored. See application/services/account/AuthService.py.
    AUTH_COOKIE_NAME: str = "nlm_session"
    # True once the app is served over HTTPS: the browser then never sends the
    # cookie over plain HTTP. Off by default because nginx here listens on 80.
    AUTH_COOKIE_SECURE: bool = False
    # A session lasts this long since the last request (sliding expiry).
    AUTH_SESSION_DAYS: int = 14
    AUTH_PASSWORD_MIN_LENGTH: int = 8
    # This many failed logins for one email locks it for the lockout window.
    AUTH_LOGIN_MAX_FAILURES: int = 5
    AUTH_LOGIN_LOCKOUT_MINUTES: int = 15
    # /docs, /redoc and /openapi.json. Off in production: they list every route.
    DOCS_ENABLED: bool = False

    # --- Visualization (the operations tools, inside the app) ----------------
    # A button in the top bar opens Grafana, Langfuse, Flower and RabbitMQ's
    # management UI inside the app, so nobody has to know four ports. Off by
    # default: the sub-path URLs below only exist behind nginx, which serves
    # them from this origin and checks the app's session first (see
    # Docker/services/nginx/gateway.conf). An empty URL hides that tab.
    VISUALIZATION_ENABLED: bool = False
    VISUALIZATION_GRAFANA_URL: str = "/grafana/"
    VISUALIZATION_FLOWER_URL: str = "/flower/"
    VISUALIZATION_RABBITMQ_URL: str = "/rabbitmq/"
    # Langfuse as the *browser* reaches it (LANGFUSE_HOST below is how the
    # app reaches it, inside the compose network). Its image cannot be served
    # under a sub-path, so it is framed from its own origin and keeps its own
    # sign-in.
    VISUALIZATION_LANGFUSE_URL: str = "http://localhost:3001"
    # LANGFUSE_INIT_PROJECT_ID in Docker/env/.env.langfuse.
    VISUALIZATION_LANGFUSE_PROJECT_ID: str = "notebookllm-minus"

    # --- tracing + prompt versioning (Langfuse) ------------------------------
    # Self-hosted Langfuse from Docker/docker-compose.yml. Off by default, and
    # also off whenever either key is missing, so tests and a bare dev box
    # never try to export. See shared/utils/tracing.py.
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_HOST: str = "http://langfuse-web:3000"
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_ENVIRONMENT: str = "development"
    # Fraction of traces kept. 1.0 keeps every chat; lower it on a busy box.
    LANGFUSE_SAMPLE_RATE: float = 1.0
    # Send prompt and answer text. Off keeps timings, models and token counts
    # and replaces every input/output with "[redacted]".
    LANGFUSE_CAPTURE_IO: bool = True
    # Prompts are fetched from Langfuse by this label, so promoting a new
    # version is a label move in the UI. The local locale files stay the
    # fallback whenever Langfuse is off or unreachable.
    LANGFUSE_PROMPTS_ENABLED: bool = True
    LANGFUSE_PROMPT_LABEL: str = "production"
    LANGFUSE_PROMPT_CACHE_TTL: int = 60
    # USD per million tokens, keyed "<backend>/<model id>" with wildcards, e.g.
    # {"nvidia/meta/*": {"input": 0.02, "output": 0.05}}. Added to the built-in
    # table in shared/utils/model_pricing.py (local Ollama models cost 0). A
    # priced model's generations carry cost_details to Langfuse; an unpriced
    # one is left for Langfuse's own price list (OpenAI, Anthropic, Google).
    MODEL_PRICES: dict[str, dict[str, float]] = {}

    # --- task queue / Celery -------------------------------------------------
    # Broker: RabbitMQ — celery_broker_url assembles these into an amqp:// DSN.
    CELERY_USERNAME: str = "appuser"
    CELERY_PASSWORD: str = "change-me"
    CELERY_HOST: str = "localhost"
    CELERY_PORT: int = 5672
    CELERY_VHOST: str = "/"  # RabbitMQ virtual host
    # RabbitMQ's management HTTP API, which Flower's Broker tab reads queue
    # depths from. Same host and credentials as the broker above.
    CELERY_MANAGEMENT_PORT: int = 15672
    # management.path_prefix in rabbitmq.conf, when the management UI is
    # served under a sub-path (compose: "/rabbitmq", so nginx can put it
    # inside the app). Empty for a RabbitMQ at its default root.
    CELERY_MANAGEMENT_PATH_PREFIX: str = ""

    # Result backend: Redis — celery_result_backend_url builds the redis:// DSN.
    CELERY_BACKEND_HOST: str = "localhost"
    CELERY_BACKEND_PORT: int = 6379
    CELERY_BACKEND_PASSWORD: str | None = None
    CELERY_BACKEND_DB: int = 0  # logical Redis database index (0-15)

    # Worker / serialisation — passed directly to the Celery app config dict.
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: list = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_ENABLE_UTC: bool = True
    # Sized from the slowest real ingestion measured, not from a round number.
    # A 222-page scanned Arabic book on the 2-vCPU deployment box needs 214
    # pages of OCR at ~5.3 s/page with two workers -- about 1136s. At the old
    # 600/540 the task was killed at 539s every time, three uploads running,
    # and the document was left listed with zero chunks and nothing in the UI
    # to say why. That server's cores are ~3.4x slower than a laptop's (a
    # fixed 8M-iteration loop: 1.48s there against 0.44s here), so the limit
    # has to be set for the slow machine or the feature only works in
    # development.
    #
    # 1800 leaves ~60% headroom over the measured worst case for a document
    # that is entirely scanned. Ingestion is a background task nobody waits
    # on, so a generous ceiling costs nothing; the cost of a tight one is a
    # silently broken document.
    CELERY_TASK_TIME_LIMIT: int = 1860  # hard wall-clock limit per task (seconds)
    # The limit that actually matters for correctness. The hard limit above is
    # a SIGKILL of the worker child: it stops the task, but it also skips every
    # `finally` on the way out — the DB disconnect in application/tasks/jobs/process.py and the
    # provider-pool close in application/tasks/jobs/index.py — so each hard kill leaked a
    # connection and a pool. The soft limit raises SoftTimeLimitExceeded inside
    # the task instead, so cleanup runs and the failure is recorded; the hard
    # limit stays as the backstop for a task that ignores it. Must be lower
    # than CELERY_TASK_TIME_LIMIT or it can never fire.
    CELERY_TASK_SOFT_TIME_LIMIT: int = 1800
    # The Studio task's own soft limit, apart from every other task's. With the
    # model's thinking on (see STUDIO_NUM_CTX), a fresh notebook's set means
    # summarising every chunk first: ~80 chunks on gemma4:e4b took 30-70 s per
    # batch of summaries and ~22 s per batch of items, past the 540 s a
    # deployment may set CELERY_TASK_SOFT_TIME_LIMIT to for ingest. Same rule
    # as that one: below CELERY_TASK_TIME_LIMIT, or cleanup never runs.
    STUDIO_TASK_SOFT_TIME_LIMIT: int = 1800
    # Report STARTED once a worker picks a task up. Off by default in Celery,
    # which is why a running task and a queued one were both PENDING — the
    # single most misleading thing the status endpoints did.
    CELERY_TASK_TRACK_STARTED: bool = True
    # How long a finished result stays readable (seconds). Celery's default is
    # one day, unset and therefore invisible; a result that expired used to
    # reappear as PENDING, indistinguishable from "still queued". Expiry is now
    # reported as UNKNOWN instead, so this is a retention choice rather than a
    # correctness one — longer costs Redis memory, since results carry the
    # task's return payload.
    CELERY_RESULT_EXPIRES: int = 604800  # 7 days
    # Emit task-* events. Required by Flower for any task history at all, and
    # by the sent-event below for tasks that never reach a worker.
    CELERY_WORKER_SEND_TASK_EVENTS: bool = True
    # Emit task-sent when the *client* publishes, not when a worker receives.
    # This is what makes a task that no worker ever picked up visible.
    CELERY_TASK_SEND_SENT_EVENT: bool = True

    # --- maintenance sweep ---------------------------------------------------
    # How often the sweep runs. The table is append-only during normal use, so
    # without this it grows for the life of the deployment.
    CELERY_MAINTENANCE_INTERVAL_HOURS: int = 24
    # How long a finished task's row is kept. Defaults to the same week as
    # CELERY_RESULT_EXPIRES: past that point Celery has already forgotten the
    # result, so a row that outlived it can no longer be cross-checked against
    # anything and is only taking up space.
    CELERY_TASK_RETENTION_DAYS: int = 7
    # Acknowledge a message only after the task finishes, so a worker killed
    # mid-ingest returns the job to the queue instead of losing it. This was
    # False while the comment beside it described the True behaviour; True is
    # what the rest of this config already assumes — the
    # cancel_long_running_tasks_on_connection_loss setting below exists
    # specifically to stop late-acked tasks running twice after a reconnect.
    # The ingestion tasks tolerate redelivery: process skips assets that are
    # already chunked, and index upserts on a deterministic point id.
    CELERY_TASK_ACKS_LATE: bool = True
    # 1, and it must match the `--concurrency` the compose command passes to
    # the process worker -- the OCR pool divides the machine by this number, so
    # a mismatch either over-subscribes the cores or leaves them idle.
    #
    # How many tasks one worker process runs at once. 0 means "every CPU this
    # process may actually use" and resolves through `available_cpus` below.
    # Read it through the `celery_worker_concurrency` property, never off this
    # field: the field is what an operator wrote, the property is what it
    # resolves to.
    #
    # It used to be 1, on the reasoning that ingestion was already parallel
    # *inside* one document, so a second concurrent document only halved the
    # cores each got. That reasoning expired when extraction moved onto the
    # queue. A document is now a group of independent parse batches rather
    # than one task running its own pool, so the parallelism lives in the
    # worker rather than in the task -- and at concurrency 1 a 274-page book
    # is extracted one batch at a time by one process, which is the serial
    # behaviour the fan-out existed to remove.
    #
    # 2 rather than 0, because the limit here is memory rather than CPU: every
    # concurrent slot is a prefork process holding the asset's bytes and a
    # batch of parsed pages, so "one per core" on a 24-core box with a large
    # PDF is 24 copies of it resident at once -- the shape of the OOM that
    # OCR_WORKERS already carries a warning about. Two is enough to keep the
    # postprocess queue fed while a batch is being parsed. Raise it, or set 0
    # for one per available CPU, on a box with the memory to back it.
    CELERY_WORKER_CONCURRENCY: int = 2

    # Broker connection resilience
    # Retry connecting to the broker if it is not up when the worker starts.
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    # Retry after a mid-run connection loss (not just at startup).
    CELERY_BROKER_CONNECTION_RETRY: bool = True
    # Seconds to wait between consecutive retry attempts.
    CELERY_BROKER_CONNECTION_RETRY_DELAY: int = 2
    # Maximum number of retry attempts before giving up. 0 means retry forever.
    CELERY_BROKER_CONNECTION_MAX_RETRIES: int = 10

    # Transport-level options passed verbatim to the broker / backend drivers.
    # Kept as dicts here; override per-deployment if the transport needs extras
    # (e.g. {"visibility_timeout": 3600} for a Redis broker).
    CELERY_BROKER_TRANSPORT_OPTIONS: dict = {}
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS: dict = {}

    # Revoke and stop long-running tasks when the worker loses its broker
    # connection, so they don't run silently after a reconnect.
    CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS: bool = True

    # --- Flower (Celery dashboard) -------------------------------------------
    #
    # Flower reads FLOWER_*-prefixed environment variables directly, so these
    # are declared here for documentation and validation rather than because
    # anything in this application passes them on. Keeping them in Settings
    # means .env.example describes the whole stack in one place, and a typo in
    # a port shows up as a validation error instead of a container that binds
    # somewhere unexpected.
    FLOWER_PORT: int = 5555
    # Changing this alone is not enough: the published port and the container
    # healthcheck in docker-compose.yml both name 5555 literally, because a
    # Compose `ports:` mapping cannot read a value out of env_file.
    #
    # "user:password", or empty for no authentication — which matches the rest
    # of this stack, where Grafana, Prometheus and the RabbitMQ management UI
    # are all unauthenticated on the host.
    #
    # The empty default is only safe because this value is *not* exported to
    # Flower. Setting FLOWER_BASIC_AUTH="" in the environment does not disable
    # auth: Flower reads it as "auth enabled, no valid users" and answers 401
    # everywhere except /healthcheck — so the container passes its health probe
    # while the dashboard is entirely unreachable. env/.env.app therefore keeps
    # the variable commented out rather than set to "".
    FLOWER_BASIC_AUTH: str = ""
    # Keep task history across restarts. Off by default in Flower, and the
    # default is a poor fit here: a dashboard whose whole purpose is showing
    # tasks that failed or never finished is least useful right after the
    # restart that a failure tends to cause.
    FLOWER_PERSISTENT: bool = True
    FLOWER_DB: str = "/app/flower/flower.db"
    # Ring-buffer size. Each retained task holds its args and result, and the
    # ingest tasks return chunk counts rather than chunk text, so this is small.
    FLOWER_MAX_TASKS: int = 10000
    # Drop workers that have not been seen for this many seconds, so containers
    # replaced by a redeploy stop appearing as live capacity.
    FLOWER_PURGE_OFFLINE_WORKERS: int = 300

    # classic | quorum. Quorum is not just the modern default — it is what
    # stops Celery using RabbitMQ's deprecated `global_qos`. Celery sets the
    # global QoS flag on RabbitMQ unless it detects a quorum queue
    # (celery/worker/consumer/tasks.py), and RabbitMQ has announced global QoS
    # for removal. Classic mirrored queues are already gone in 4.x.
    # Changing this on a live broker needs the existing queues deleted first:
    # a queue's type is fixed at declaration and redeclaring with a different
    # x-queue-type fails with PRECONDITION_FAILED.
    CELERY_TASK_QUEUE_TYPE: str = "quorum"

    # Prefix used in every task and queue name. Queue names are derived as
    # ``CELERY_PROJECT_NAME.<task function name>`` below.
    CELERY_PROJECT_NAME: str = "notebookllm"
    # Default queue name for tasks that do not declare one explicitly.
    CELERY_TASK_DEFAULT_QUEUE: str | None = None
    # Dedicated queues keep CPU-heavy ingestion, model calls, and destructive
    # maintenance from competing for the same worker slots.
    CELERY_QUEUE_PROCESS: str | None = None
    # The correction pass gets its own, because it is the one stage that waits
    # on a model rather than on a CPU: tens of seconds a page, where parsing is
    # milliseconds. Sharing the process queue would mean one large document's
    # corrections blocking every other document's extraction behind them.
    CELERY_QUEUE_POSTPROCESS: str | None = None
    CELERY_QUEUE_INDEX: str | None = None
    CELERY_QUEUE_STUDIO: str | None = None
    CELERY_QUEUE_MAINTENANCE: str | None = None
    # Its own queue for the same reason Studio has one: extraction waits
    # seconds on a model API, and a queue with no `-Q` consumer of its own
    # accepts messages nothing will ever process.
    CELERY_QUEUE_MEMORY: str | None = None
    # One queue per guardrail stage, each with its own worker in compose: the
    # input check sits in front of every answer and must not wait behind
    # output checks, which arrive several per answer.
    CELERY_QUEUE_GUARDRAIL_INPUT: str | None = None
    CELERY_QUEUE_GUARDRAIL_OUTPUT: str | None = None

    # --- normalizers ---------------------------------------------------------
    @model_validator(mode="after")
    def _derive_celery_queue_names(self):
        """Use ``<project>.<task function>`` for unset queue overrides."""
        defaults = {
            "CELERY_TASK_DEFAULT_QUEUE": f"{self.CELERY_PROJECT_NAME}.default",
            "CELERY_QUEUE_PROCESS": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.PROCESS.value}",
            "CELERY_QUEUE_POSTPROCESS": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.POSTPROCESS.value}",
            "CELERY_QUEUE_INDEX": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.INDEX.value}",
            "CELERY_QUEUE_STUDIO": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.GENERATE_ARTIFACT.value}",
            "CELERY_QUEUE_MAINTENANCE": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.MAINTENANCE.value}",
            "CELERY_QUEUE_MEMORY": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.MEMORY.value}",
            "CELERY_QUEUE_GUARDRAIL_INPUT": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.GUARDRAIL_INPUT.value}",
            "CELERY_QUEUE_GUARDRAIL_OUTPUT": f"{self.CELERY_PROJECT_NAME}.{CeleryTaskFunction.GUARDRAIL_OUTPUT.value}",
        }
        for field, value in defaults.items():
            if getattr(self, field) is None:
                setattr(self, field, value)
        return self

    @model_validator(mode="after")
    def _check_soft_time_limit(self):
        """A soft limit at or above the hard one can never fire.

        Worth failing startup over rather than warning: the symptom of getting
        this wrong is silent — tasks go back to being SIGKILLed with their
        cleanup skipped, which is exactly the bug the soft limit was added to
        fix, and nothing in the logs would say so.
        """
        for name in ("CELERY_TASK_SOFT_TIME_LIMIT", "STUDIO_TASK_SOFT_TIME_LIMIT"):
            if getattr(self, name) >= self.CELERY_TASK_TIME_LIMIT:
                raise ValueError(
                    f"{name} ({getattr(self, name)}) must be "
                    f"below CELERY_TASK_TIME_LIMIT ({self.CELERY_TASK_TIME_LIMIT}); "
                    "otherwise the hard kill always wins and task cleanup never runs"
                )
        return self

    # `mode="before"` so they run ahead of the enum check: a .env saying
    # "Ollama" or " postgres " is a spelling difference, not a wrong value.
    #
    # Computed from the field annotations captured above in this same class
    # body — `__annotations__` is a real, already-populated dict at this point
    # in the class's execution, not a hardcoded guess at which fields matter.
    _LOWERCASE_FIELDS = _enum_choice_fields(__annotations__, exclude=_UPPERCASE_FIELDS)

    @field_validator(*_LOWERCASE_FIELDS, mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _uppercase(cls, value: object) -> object:
        # The one option whose canonical spelling is upper case, because that
        # is how `logging` names its levels.
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("NVIDIA_API_BASE_URL", mode="before")
    @classmethod
    def _default_nvidia_endpoint(cls, value: object) -> object:
        # `NVIDIA_API_BASE_URL = ""` in a .env reads as "I am not setting
        # this", the way every other commented-out or emptied key does — and
        # for this one field an empty string is not a usable value.
        return value or NVIDIA_DEFAULT_BASE_URL

    @field_validator("OPENROUTER_API_BASE_URL", mode="before")
    @classmethod
    def _default_openrouter_endpoint(cls, value: object) -> object:
        # Same reasoning as NVIDIA_API_BASE_URL: an emptied line means "unset",
        # and an empty endpoint would make the client dial the OpenAI default
        # with an OpenRouter key.
        return value or OPENROUTER_DEFAULT_BASE_URL

    @field_validator("GENERATION_THINKING", mode="before")
    @classmethod
    def _resolve_thinking_alias(cls, value: object) -> object:
        # The alias table lives on the enum itself (enums/LLMChattingEnum.py),
        # next to the members it resolves onto — not here, where it would be
        # the one piece of Settings that knows something about ThinkingLevel
        # rather than the other way around.
        return ThinkingLevel.from_alias(value)

    # --- derived paths and DSNs ----------------------------------------------

    @property
    def log_file_path(self) -> Path:
        """Absolute path of the log file, anchoring a relative LOG_DIR to src/."""
        log_dir = Path(self.LOG_DIR)
        if not log_dir.is_absolute():
            log_dir = SRC_DIR / log_dir
        return log_dir / self.LOG_FILE_NAME

    # @property
    # def vector_db_path(self) -> Path:
    #     """Absolute path of the embedded vector store, anchored to src/.

    #     Same treatment as log_file_path, and for the same reason: the app is
    #     launched from src/ but need not be, and the store must not follow CWD.
    #     """
    #     db_path = Path(self.VECTOR_DB_PATH)
    #     if not db_path.is_absolute():
    #         db_path = SRC_DIR / db_path
    #     return db_path

    def _postgres_url(self, driver: str) -> str:
        """DSN for *driver*, with the credentials percent-encoded.

        quote_plus because a password is free text: an unescaped `@` or `/` in
        it silently reshapes the URL and the connection fails somewhere else
        entirely.
        """
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        database = quote_plus(self.POSTGRES_DB)
        return f"{driver}://{user}:{password}" f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{database}"

    @property
    def postgres_url(self) -> str:
        """Plain DSN — psql, and Alembic's offline mode."""
        return self._postgres_url("postgresql")

    @property
    def postgres_async_url(self) -> str:
        """The one the app connects with: SQLAlchemy over asyncpg."""
        return self._postgres_url("postgresql+asyncpg")

    @property
    def mongo_uri(self) -> str:
        """The DSN for MongoDB."""
        user = quote_plus(self.MONGO_USER)
        password = quote_plus(self.MONGO_PASSWORD)
        return f"mongodb://{user}:{password}@{self.MONGO_HOST}:{self.MONGO_PORT}"

    @property
    def vector_db_url(self) -> str | None:
        """The URL for the vector database (Qdrant)."""
        if self.VECTOR_DB_HOST and self.VECTOR_DB_PORT:
            return f"http://{self.VECTOR_DB_HOST}:{self.VECTOR_DB_PORT}"
        return None

    @property
    def ollama_base_url(self) -> str:
        """The URL a client should actually connect to.

        The override wins when set — that is the whole mechanism
        ProviderCache uses to point one model's client at the cloud host
        while every other setting still comes from this same object. Absent
        an override, it is the local host built from OLLAMA_HOST/PORT.
        """
        if self.OLLAMA_BASE_URL_OVERRIDE:
            return self.OLLAMA_BASE_URL_OVERRIDE
        return f"http://{self.OLLAMA_HOST}:{self.OLLAMA_PORT}"

    @property
    def available_cpus(self) -> int:
        """How many CPUs this process can actually use, right now.

        Computed, never a constant: the answer differs between a laptop, a
        `--cpus=2` container on a 24-core host, and a pod pinned with
        `--cpuset-cpus`, and only two of those three are visible to
        `os.cpu_count()`. `cpu_count` reads the affinity mask and the cgroup
        quota and takes the smaller -- see utils/resources for why neither
        alone is enough.

        Exposed as a setting-shaped property so anything sizing a pool asks
        the configuration rather than measuring the machine for itself, which
        is what let two pools disagree about how big the box was.
        """
        return max(1, cpu_count())

    @property
    def celery_worker_concurrency(self) -> int:
        """How many tasks one worker process runs at once.

        `CELERY_WORKER_CONCURRENCY` resolved: 0 means one per available CPU,
        anything above 0 is honoured exactly as written -- including a number
        larger than the machine has. An operator who sets that can see the box
        and may know something this cannot, and silently overriding them is how
        a 2-vCPU server ends up unable to be told to use both of its cores. The
        same policy as OCR_WORKERS.

        Every reader goes through here rather than through the raw field, so
        "0" cannot leak out as a literal concurrency of zero -- which Celery
        reads as "spawn no workers": a worker that starts, reports ready, and
        consumes nothing, leaving every ingestion QUEUED.
        """
        configured = self.CELERY_WORKER_CONCURRENCY

        return configured if configured > 0 else self.available_cpus

    @property
    def rabbitmq_management_api_url(self) -> str:
        """RabbitMQ management API URL, for Flower's Broker tab.

        Built from the broker credentials rather than configured separately, so
        the two cannot drift -- the same reason Flower runs from the app image.
        """
        user = quote_plus(self.CELERY_USERNAME)
        password = quote_plus(self.CELERY_PASSWORD)
        prefix = self.CELERY_MANAGEMENT_PATH_PREFIX.strip("/")
        path = f"{prefix}/api/" if prefix else "api/"
        return f"http://{user}:{password}@{self.CELERY_HOST}:{self.CELERY_MANAGEMENT_PORT}/{path}"

    @property
    def celery_broker_url(self) -> str:
        """AMQP broker URL for Celery (RabbitMQ).

        Credentials are percent-encoded so a password containing reserved URI
        characters (@ / : ?) does not silently reshape the URL.
        The default vhost "/" is encoded as "%2F" — the canonical form in
        AMQP URLs; any other vhost is encoded the same way.
        """
        user = quote_plus(self.CELERY_USERNAME)
        password = quote_plus(self.CELERY_PASSWORD)
        vhost = quote_plus(self.CELERY_VHOST)
        return f"amqp://{user}:{password}" f"@{self.CELERY_HOST}:{self.CELERY_PORT}/{vhost}"

    @property
    def celery_result_backend_url(self) -> str:
        """Redis URL for the Celery result backend.

        When CELERY_BACKEND_PASSWORD is set the auth segment is included
        (``redis://:<password>@host:port/db``); when it is absent the segment
        is omitted entirely so a Redis server without ``requirepass`` is not
        sent an empty AUTH command that it would reject.
        """
        host = self.CELERY_BACKEND_HOST
        port = self.CELERY_BACKEND_PORT
        db = self.CELERY_BACKEND_DB
        if self.CELERY_BACKEND_PASSWORD:
            password = quote_plus(self.CELERY_BACKEND_PASSWORD)
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"


@lru_cache
def get_settings() -> Settings:
    """The one Settings instance for this process.

    Cached because it is a FastAPI ``Depends`` — without this, every request
    touching it re-reads .env and re-runs every validator. The consequence is
    that .env edits need a restart, which was already true in practice since
    main.py captures SETTINGS at import time.
    """
    return Settings()
