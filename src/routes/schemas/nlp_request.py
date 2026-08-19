from pydantic import BaseModel, Field


class PushRequest(BaseModel):
    """Body for POST /nlp/index/push/{project_id}."""

    asset_id: str | None = Field(
        None, description="Index only this asset's chunks; every asset if omitted"
    )

    # Drops the collection and rebuilds it. Needed when the embedding model or
    # its vector size changes, since both are fixed at collection creation.
    reset: bool = False

    # Chunks per embed + upsert round trip. Capped because the whole batch is
    # held in memory as text *and* as float vectors at the same time.
    batch_size: int = Field(64, gt=0, le=512)


class SearchRequest(BaseModel):
    """Body for POST /nlp/index/search/{project_id}."""

    # min_length keeps an empty query from being embedded into a meaningless
    # vector that still returns confident-looking nearest neighbours.
    text: str = Field(..., min_length=1)

    # Qdrant rejects limit=0 outright, so bound it here and return a 422 that
    # names the field instead of a 503 from the vector store.
    limit: int = Field(5, gt=0, le=100)
