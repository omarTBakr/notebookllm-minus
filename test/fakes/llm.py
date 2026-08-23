"""Stand-ins for the chat and embedding providers.

The real interfaces do the useful work in concrete methods and leave one
abstract hook each, so a fake only has to fill the hook — `stream_text`,
message building and validation all come for free and stay under test.
"""

from collections.abc import AsyncIterator

from factories.llmchatting import LLMChattingInterface
from factories.llmembedding import LLMEmbeddingInterface


class FakeChatClient(LLMChattingInterface):
    """Returns a canned answer and records what it was asked."""

    def __init__(self, reply: str = "an answer", model_id: str = "fake-chat", **kwargs):
        super().__init__(model_id=model_id, **kwargs)
        self.reply = reply
        self.calls: list[dict] = []
        self.closed = False

    async def _generate_text(self, messages, max_tokens, temperature) -> str:
        self.calls.append(
            {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        )
        return self.reply

    async def aclose(self) -> None:
        self.closed = True


class FakeStreamingChatClient(FakeChatClient):
    """Yields thinking then content, the way a reasoning model does."""

    async def _stream_text(self, messages, max_tokens, temperature) -> AsyncIterator[dict]:
        self.calls.append({"messages": messages, "max_tokens": max_tokens,
                           "temperature": temperature})
        yield {"kind": "thinking", "text": "hmm"}
        for word in self.reply.split():
            yield {"kind": "content", "text": word + " "}


class FailingChatClient(FakeChatClient):
    """Raises whatever it was handed, for the error paths."""

    def __init__(self, error: Exception, **kwargs):
        super().__init__(**kwargs)
        self.error = error

    async def _generate_text(self, messages, max_tokens, temperature) -> str:
        raise self.error


class FakeEmbeddingClient(LLMEmbeddingInterface):
    """Deterministic vectors of the declared width."""

    def __init__(self, embedding_size: int = 8, model_id: str = "fake-embed", **kwargs):
        super().__init__(model_id=model_id, embedding_size=embedding_size, **kwargs)
        self.calls: list[list[str]] = []
        self.closed = False

    async def _embed(self, texts, input_type):
        self.calls.append(list(texts))
        # Vary by text length so different chunks get different vectors.
        return [[float(len(t) + i) for i in range(self.embedding_size)] for t in texts]

    async def aclose(self) -> None:
        self.closed = True


class FakeProviderCache:
    """What routes actually use: two lookups and a close."""

    def __init__(self, chatting=None, embedding=None):
        self._chatting = chatting or FakeChatClient()
        self._embedding = embedding or FakeEmbeddingClient()
        self.asked_for: list = []

    def chatting(self, model_id=None):
        self.asked_for.append(("chatting", model_id))
        return self._chatting

    def embedding(self, model_id=None, dimensions=None):
        self.asked_for.append(("embedding", model_id, dimensions))
        return self._embedding

    async def aclose_all(self) -> None:
        await self._chatting.aclose()
        await self._embedding.aclose()
