from .OpenAIChatProvider import OpenAIChatProvider


class NvidiaChatProvider(OpenAIChatProvider):
    """Text generation via NVIDIA's hosted NIM endpoints.

    NVIDIA serves the OpenAI Chat Completions API verbatim, so there is
    nothing to translate and nothing to configure here: the endpoint arrives
    as ``base_url`` from NVIDIA_API_BASE_URL, exactly as their own snippet
    passes it —

        OpenAI(base_url=..., api_key=NVIDIA_API_KEY)

    so the class is the vendor's *name*, and nothing else. It exists rather
    than being ``openai`` plus an ``OPENAI_API_BASE_URL`` override so that
    both vendors can be configured at once: they are separate accounts with
    separate catalogues, and a NIM model id carries its publisher
    ("meta/llama-3.2-11b-vision-instruct") that means nothing to OpenAI.

    See :class:`NvidiaEmbeddingProvider` for the embedding half.
    """

    _VENDOR = "NVIDIA"

    # NIM schemas are strict about unknown fields, and several models reject
    # max_completion_tokens outright — "extra_forbidden ... max_completion_tokens"
    # is a 400, not a warning. max_tokens is accepted across the fleet.
    _MAX_TOKENS_FIELD = "max_tokens"
