"""What a model call costs, from its token counts.

Langfuse prices a generation itself only when it recognises the model name:
its built-in list covers OpenAI, Anthropic and Google. Everything else this
app runs (local Ollama models, NVIDIA NIM, OpenRouter's catalogue) reached
Langfuse with token counts but no cost. This table fills that gap: for a model
it prices, each generation is sent with ``cost_details`` (input, output and
total, in USD); for any other model nothing is sent and Langfuse falls back to
its own list.

Keys are ``<backend>/<model id>`` with shell-style wildcards, for example
``ollama/*`` or ``openrouter/meta-llama/*``. Prices are USD per million tokens.
The most specific matching key wins: an exact key before any pattern, then the
longest pattern. MODEL_PRICES in .env adds or overrides entries:

    MODEL_PRICES={"nvidia/meta/llama-3.1-8b-instruct": {"input": 0.02, "output": 0.05}}
"""

from fnmatch import fnmatchcase

#: USD per million tokens. Local models run on your own hardware, so they cost
#: nothing per call; set a notional price in MODEL_PRICES to track GPU time.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "ollama/*": {"input": 0.0, "output": 0.0},
}


def backend_of(provider_class_name: str) -> str:
    """ "OllamaChatProvider" -> "ollama", "OpenRouterChatProvider" -> "openrouter"."""
    return provider_class_name.removesuffix("ChatProvider").lower()


def price_table(overrides: dict | None = None) -> dict[str, dict[str, float]]:
    table = {key.lower(): dict(value) for key, value in DEFAULT_PRICES.items()}
    for key, value in (overrides or {}).items():
        table[str(key).lower()] = {
            "input": float(value.get("input", 0.0)),
            "output": float(value.get("output", 0.0)),
        }
    return table


def price_for(backend: str, model_id: str, table: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """The per-million-token price of *model_id* on *backend*, or None if unpriced."""
    name = f"{backend}/{model_id}".lower()

    if name in table:
        return table[name]

    patterns = [key for key in table if any(ch in key for ch in "*?[") and fnmatchcase(name, key)]
    if not patterns:
        return None
    return table[max(patterns, key=len)]


def cost_details(
    backend: str,
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    overrides: dict | None = None,
) -> dict[str, float] | None:
    """USD cost of one call, shaped as Langfuse's ``cost_details``, or None.

    None when the model is not in the table (Langfuse prices it, if it can) or
    when the vendor reported no tokens at all.
    """
    if input_tokens is None and output_tokens is None:
        return None

    price = price_for(backend, model_id, price_table(overrides))
    if price is None:
        return None

    input_cost = (input_tokens or 0) * price["input"] / 1_000_000
    output_cost = (output_tokens or 0) * price["output"] / 1_000_000
    return {"input": input_cost, "output": output_cost, "total": input_cost + output_cost}
