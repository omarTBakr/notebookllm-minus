"""Who builds the ANN index, now that index_chunks no longer does.

The build moved out of NLPController.index_chunks into build_index() so it
could be its own link in the ingestion chain. That leaves two obligations, and
missing either is silent — a collection with no index still answers every
search, correctly, by exact scan:

* every path that writes vectors must build the index afterwards, and
* no path may build it twice, which on pgvector means a second CREATE INDEX
  and on Qdrant a second HNSW build over the same points.
"""

from bson.objectid import ObjectId

from controllers import NLPController
from test.fakes.db import FakeVectorRepository
from test.fakes.llm import FakeEmbeddingClient


def _controller(embedding_size=8):
    vectors = FakeVectorRepository()
    return NLPController(
        embedding_client=FakeEmbeddingClient(embedding_size=embedding_size),
        vectordb_client=vectors,
    ), vectors


class _Chunks:
    """The two ChunkModel methods index_chunks reaches for, and nothing else."""

    def __init__(self, chunks=()):
        self.chunks = list(chunks)

    async def count_project_chunks(self, project_object_id, asset_id=None):
        return len(self.chunks)

    async def iter_project_chunks(self, project_object_id, asset_id=None):
        for chunk in self.chunks:
            yield chunk


async def test_index_chunks_no_longer_builds_the_index():
    """The regression that would make the new chain link redundant, and would
    quietly build the index twice per upload."""
    controller, vectors = _controller()

    await controller.index_chunks(
        chunk_model=_Chunks(),
        project_object_id=ObjectId(),
        project_id="p1",
    )

    assert vectors.indexed == []


async def test_build_index_asks_the_backend_at_the_models_own_width():
    """The width comes from the embedding client, not from Settings: it is what
    validated the model's real output, so the index cannot be built at a width
    the collection was not created at."""
    controller, vectors = _controller(embedding_size=2048)

    result = await controller.build_index("p1")

    assert vectors.indexed == [
        {
            "collection_name": "project_p1",
            "embedding_size": 2048,
            "index_type": None,
            "reset": False,
        }
    ]
    assert result == {"collection": "project_p1", "vector_size": 2048, "index_built": True}


async def test_a_backend_that_declines_to_index_is_not_a_failure():
    """pgvector will not index past 4000 dimensions. An exact scan is a slow
    search, not a broken one, so this reports and returns rather than raising —
    the alternative is that a wide embedding model cannot be used at all."""
    controller, vectors = _controller()

    async def decline(**kwargs):
        vectors.indexed.append(kwargs)
        return False

    vectors.create_index = decline

    result = await controller.build_index("p1")

    assert result["index_built"] is False


async def test_build_index_names_the_same_collection_index_chunks_wrote():
    """Two call sites deriving a collection name independently is how the build
    ends up on a collection nothing searches."""
    controller, vectors = _controller()

    await controller.index_chunks(
        chunk_model=_Chunks(),
        project_object_id=ObjectId(),
        project_id="a b/c",
    )
    await controller.build_index("a b/c")

    assert vectors.indexed[0]["collection_name"] in vectors.collections


# --- end to end, through the route that queues the chain ---------------------


async def test_uploading_a_document_builds_the_index_exactly_once(ingest, client, seed, fake_db):
    """Both halves of the obligation in one assertion: the upload path still
    ends with an index, and splitting the build out did not leave the old call
    behind as well."""
    await ingest("c1", {"file": ("new.txt", b"the note body", "text/plain")})

    assert len(fake_db.vectors().indexed) == 1
