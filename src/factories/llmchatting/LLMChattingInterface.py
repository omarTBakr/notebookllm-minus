"""The contract every text-generation backend implements.

Callers speak one dialect — a list of ``{"role": ..., "content": ...}`` dicts
using :class:`~enums.ChatRole` — and each provider translates it into whatever
its own SDK wants. That translation is the whole point of this layer: swapping
Anthropic for Cohere should be an ``.env`` edit, not a rewrite.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from time import perf_counter

from enums import ChatRole
from utils import get_logger


class LLMChattingInterface(ABC):
    """Base for chat providers.

    Concrete, not purely abstract, in the shape of ``BaseController``: the
    constructor holds the settings every provider needs and hands down a logger
    named after the subclass's own module.
    """

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        default_max_tokens: int = 1024,
        default_temperature: float = 0.1,
    ) -> None:

        # api_key is optional because a locally hosted backend (Ollama) has no
        # key to give; the factory decides which providers require one.
        self.api_key = api_key

        self.model_id = model_id

        self.default_max_tokens = default_max_tokens

        self.default_temperature = default_temperature

        # e.g. "factories.llmchatting.OpenAIChatProvider"
        self.logger = get_logger(type(self).__module__)

    async def generate_text(
        self,
        prompt: str,
        chat_history: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Answer *prompt*, optionally continuing *chat_history*.

        Returns the assistant's text. Raises ``LLMProviderError`` if the vendor
        call fails or comes back without usable text — never returns ``None``
        or an empty string to signal failure.

        Concrete on purpose: this normalizes the messages and records the call,
        then hands off to :meth:`_generate_text`. Putting it here rather than in
        each provider means all five log the same fields in the same shape, and
        a provider cannot forget to.
        """
        messages = self._build_messages(prompt, chat_history)

        resolved_max_tokens = max_tokens or self.default_max_tokens

        resolved_temperature = (
            temperature if temperature is not None else self.default_temperature
        )

        # Counts and sizes only — never prompt or answer text. These lines go to
        # the same file and aggregator as everything else, and user documents
        # are exactly what should not be sitting in them.
        self.logger.debug(
            "Generating text (model=%s, messages=%d, max_tokens=%d, temperature=%s)",
            self.model_id,
            len(messages),
            resolved_max_tokens,
            resolved_temperature,
            extra={"provider": type(self).__name__, "model_id": self.model_id},
        )

        started = perf_counter()

        # No try/except: a provider raises LLMProviderError and stays quiet, and
        # the handler in main.py logs it once. Catching it here to log would
        # produce two records of one failure.
        text = await self._generate_text(messages, resolved_max_tokens, resolved_temperature)

        elapsed_ms = (perf_counter() - started) * 1000

        self.logger.info(
            "Generated %d chars in %.0f ms (provider=%s, model=%s)",
            len(text),
            elapsed_ms,
            type(self).__name__,
            self.model_id,
            extra={
                "provider": type(self).__name__,
                "model_id": self.model_id,
                "duration_ms": round(elapsed_ms, 1),
                "response_chars": len(text),
            },
        )

        return text

    @abstractmethod
    async def _generate_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> str:
        """Call the vendor with already-normalized *messages* and resolved knobs.

        This is the one method a provider must write. *messages* is the neutral
        role/content format including the new user turn; translating it and the
        response is the provider's whole job.
        """

    async def stream_text(
        self,
        prompt: str,
        chat_history: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[dict]:
        """Answer *prompt*, yielding the text in pieces as it arrives.

        Same contract as generate_text — the concatenation of everything
        yielded is the answer — but the caller can show it as it lands instead
        of waiting for the whole thing.

        Yields ``{"kind": "thinking" | "content", "text": str}``. Reasoning
        models emit their scratchpad first; concatenating only the ``content``
        pieces gives the same string ``generate_text`` would have returned.

        **Not every backend truly streams.** Providers that do override
        :meth:`_stream_text`; the rest fall back to generating the full answer
        and yielding it as one piece, so this endpoint works for all of them
        and only the latency differs. Today Ollama streams and the four hosted
        providers do not.
        """
        messages = self._build_messages(prompt, chat_history)

        resolved_max_tokens = max_tokens or self.default_max_tokens

        resolved_temperature = (
            temperature if temperature is not None else self.default_temperature
        )

        self.logger.debug(
            "Streaming text (model=%s, messages=%d, max_tokens=%d, temperature=%s)",
            self.model_id,
            len(messages),
            resolved_max_tokens,
            resolved_temperature,
            extra={"provider": type(self).__name__, "model_id": self.model_id},
        )

        started = perf_counter()

        chars = 0
        chunks = 0
        thinking_chars = 0

        async for piece in self._stream_text(
            messages, resolved_max_tokens, resolved_temperature
        ):
            if piece["kind"] == "thinking":
                thinking_chars += len(piece["text"])
            else:
                chars += len(piece["text"])
                chunks += 1

            yield piece

        elapsed_ms = (perf_counter() - started) * 1000

        # chunk count is the tell for whether this actually streamed: a
        # fallback provider reports exactly 1.
        self.logger.info(
            "Streamed %d chars (+%d thinking) in %d chunk(s) over %.0f ms (provider=%s, model=%s)",
            chars,
            thinking_chars,
            chunks,
            elapsed_ms,
            type(self).__name__,
            self.model_id,
            extra={
                "provider": type(self).__name__,
                "model_id": self.model_id,
                "duration_ms": round(elapsed_ms, 1),
                "response_chars": chars,
                "thinking_chars": thinking_chars,
                "stream_chunks": chunks,
            },
        )

    async def _stream_text(
        self, messages: list[dict], max_tokens: int, temperature: float
    ) -> AsyncIterator[dict]:
        """Vendor-specific streaming, defaulting to "no streaming at all".

        Concrete rather than abstract so a provider only implements this when
        its SDK supports it. The default generates the whole answer and yields
        it once, which is correct — just not incremental, and never with
        thinking.
        """
        yield {
            "kind": "content",
            "text": await self._generate_text(messages, max_tokens, temperature),
        }

    async def aclose(self) -> None:
        """Release the client's connection pool.

        The default suits every SDK whose client exposes ``close()`` — which
        is most of them. Google and Cohere name it differently and override.
        """
        await self.client.close()

    # --- helpers shared by every provider ------------------------------------

    def _log_usage(self, input_tokens: int | None, output_tokens: int | None) -> None:
        """Record token usage when the vendor reports it.

        Every SDK names these differently and Ollama reports them only
        sometimes, so each provider digs them out and calls this to get one
        consistent line — the number worth watching when a bill looks wrong.
        """
        if input_tokens is None and output_tokens is None:
            return

        self.logger.debug(
            "Token usage (provider=%s, model=%s, input=%s, output=%s)",
            type(self).__name__,
            self.model_id,
            input_tokens,
            output_tokens,
            extra={
                "provider": type(self).__name__,
                "model_id": self.model_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

    @staticmethod
    def construct_message(role: ChatRole | str, content: str) -> dict:
        """Build one provider-neutral history entry."""
        return {"role": ChatRole(role).value, "content": content}

    def _build_messages(self, prompt: str, chat_history: list[dict] | None) -> list[dict]:
        """History plus the new user turn, in neutral form."""
        messages = list(chat_history or [])

        messages.append(self.construct_message(ChatRole.USER, prompt))

        return messages

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Lift system turns out of the message list.

        Anthropic and Google both take the system prompt as a separate
        top-level argument and reject it as a message, so those two providers
        have to pull it out. Multiple system turns are joined with newlines
        rather than silently dropping all but one.
        """
        system_parts = [m["content"] for m in messages if m["role"] == ChatRole.SYSTEM]

        rest = [m for m in messages if m["role"] != ChatRole.SYSTEM]

        return ("\n\n".join(system_parts) if system_parts else None), rest
