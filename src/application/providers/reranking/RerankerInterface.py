"""The contract every reranker implements.

Vector search ranks a passage by how close its embedding sits to the query's.
That is fast and recalls well, but it is a coarse judge: two passages about the
same topic look alike to it whether or not either one answers the question. A
cross-encoder reads the query and each passage *together* and scores how well
one answers the other. Too slow to run over a whole collection, but cheap over
the few dozen candidates vector search hands it, which is how it is used here:
search wide, rerank, keep the best ``top_k``.
"""

from abc import ABC, abstractmethod
from time import perf_counter

from shared.utils import get_logger, tracing
from shared.utils.metrics import RERANK_SECONDS


class RerankerInterface(ABC):
    """Base for rerank providers.

    Like ``LLMChattingInterface``: the public method is concrete and records
    the call (metrics, a Langfuse span), and a provider fills in one hook.
    """

    #: False for the pass-through: callers skip widening the candidate pool,
    #: since nothing would re-order it.
    ENABLED = True

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.logger = get_logger(type(self).__module__)

    async def rerank(self, query: str, texts: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
        """``(index into texts, score)`` pairs, best first, at most *top_n*.

        Scores are relevance on 0..1, higher is better. Raises whatever the
        vendor call raised; the caller decides how to degrade.
        """
        if not texts:
            return []

        provider = type(self).__name__
        started = perf_counter()

        with tracing.observation(
            "rerank",
            as_type="retriever",
            input={"query": query, "candidates": len(texts)},
            metadata={"provider": provider, "model": self.model_id},
        ) as span:
            ranked = await self._rerank(query, texts)

            RERANK_SECONDS.labels(provider).observe(perf_counter() - started)

            ranked = sorted(ranked, key=lambda pair: pair[1], reverse=True)
            if top_n is not None:
                ranked = ranked[:top_n]

            span.update(output=[{"index": i, "score": round(s, 4)} for i, s in ranked])

        self.logger.debug(
            "Reranked %d candidates in %.0f ms (provider=%s)",
            len(texts),
            (perf_counter() - started) * 1000,
            provider,
        )
        return ranked

    @abstractmethod
    async def _rerank(self, query: str, texts: list[str]) -> list[tuple[int, float]]:
        """Score every text against *query*, in any order."""

    async def aclose(self) -> None:
        return None


class NoopReranker(RerankerInterface):
    """Keeps vector order. What RERANKER_ENABLED=false builds."""

    ENABLED = False

    def __init__(self) -> None:
        super().__init__(model_id="none")

    async def _rerank(self, query: str, texts: list[str]) -> list[tuple[int, float]]:
        # Descending pseudo-scores, so sorting keeps the incoming order.
        n = len(texts)
        return [(i, (n - i) / n) for i in range(n)]
