"""Answering a question from the notebook's own documents.

`NLPService` owns the index and the search over it; `ChatService` turns
what came back into a grounded answer with citations that resolve to a real
page. Together rather than apart because they are two halves of one request —
retrieval quality is only visible in the answer, and the answer is only
defensible because of the retrieval.
"""

from .ChatService import ChatService
from .IndexService import IndexService
from .NLPService import NLPService
from .QueryDecomposer import SubQuestions, decompose_query

__all__ = ["ChatService", "IndexService", "NLPService", "SubQuestions", "decompose_query"]
