"""A mind map: topics drawn from the notebook, then grouped into branches.

Two shapes because it is built in two passes. Each batch of summaries yields
`MindMapNode`s -- topics that cite a chunk, appended as they arrive so the map
fills while it is read. When every batch is in, one call sees all the topics at
once and returns a `MindMapOutline` saying which branch each belongs to; only
that pass can see the whole notebook, so only it can choose the branches.

Both list fields are required, for the reason flashcard.py gives: with a
default, a model that echoes the schema back parses as an empty answer and is
never retried.
"""

from pydantic import BaseModel, Field, field_validator


class MindMapNode(BaseModel):
    """One topic, and the chunk it came from."""

    topic: str = Field(..., min_length=1, max_length=120)
    detail: str = Field(..., min_length=1, max_length=600)
    # Batch-local summary number, translated to the real chunk by the
    # controller -- see Flashcard.chunk_order.
    chunk_order: int = Field(..., ge=0)


class MindMapNodeSet(BaseModel):
    """What one batch of summaries is expected to yield."""

    nodes: list[MindMapNode] = Field(...)

    model_config = {
        "json_schema_extra": {
            "example": {
                "nodes": [
                    {
                        "topic": "EXAMPLE ONLY - a short topic name",
                        "detail": "EXAMPLE ONLY - one sentence on what the passage says about it",
                        "chunk_order": 1,
                    }
                ]
            }
        }
    }


class MindMapBranch(BaseModel):
    """One branch off the root, naming the topics under it by number."""

    title: str = Field(..., min_length=1, max_length=80)
    members: list[int] = Field(..., min_length=1)


class MindMapOutline(BaseModel):
    """How every topic is grouped. A topic left out lands on an "Other" branch."""

    branches: list[MindMapBranch] = Field(..., min_length=1)

    @field_validator("branches")
    @classmethod
    def _titles_are_distinct(cls, branches):
        # Two branches with one title would render as one heading owning two
        # separate groups; asking again is cheaper than merging them silently.
        titles = [b.title.strip().casefold() for b in branches]

        if len(set(titles)) != len(titles):
            raise ValueError("branch titles must be distinct")

        return branches

    model_config = {
        "json_schema_extra": {
            "example": {
                "branches": [
                    {"title": "EXAMPLE ONLY - branch name", "members": [1, 4, 7]},
                    {"title": "EXAMPLE ONLY - another branch", "members": [2, 3]},
                ]
            }
        }
    }
