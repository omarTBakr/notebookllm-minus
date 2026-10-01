"""A chunk's one-line precis, and the set a summarising call returns.

This is what makes the whole feature fit: 694 chunks do not go into a context
window and 694 summaries do, so the model is given summaries and never the
source text. The number is what ties a summary back to the chunk it describes.
"""

from pydantic import BaseModel, Field

# The list field on each set below is REQUIRED -- `Field(...)`, not
# `default_factory=list`. The difference is not stylistic and it cost a batch.
#
# With a default, *any* JSON object validates as an empty set. A model that
# echoes the schema back at you instead of filling it in -- which is exactly
# what llama-3.2-11b did with a ten-summary batch -- produces a clean parse of
# zero items, so `generate_structured` calls it a success and its repair loop
# never fires. The batch is dropped in silence and the deck is quietly half
# the size it should be.
#
# Required means the key has to be there, which is proof the model answered
# the question rather than restating it; a missing key is a ValidationError
# and gets retried with the error fed back. An *empty* list stays valid,
# because "these ten summaries support no good card" is a real answer the
# prompts explicitly invite.


class ChunkSummary(BaseModel):
    """One chunk's precis, tied to the number it was shown under.

    `num` is the batch-local number from the prompt, not a chunk_order: the
    caller knows which chunk that was, and asking the model for a database
    identifier it has never seen invites it to invent one.
    """

    num: int = Field(..., ge=1)
    summary: str = Field(..., min_length=1, max_length=1000)


class SummarySet(BaseModel):
    """What one summarising call is expected to return.

    Structured rather than prose split on newlines. A small model answering a
    ten-excerpt batch with a single line used to have that line assigned to
    every chunk in the batch -- so nine chunks carried a summary describing a
    different passage, and every card built from them cited the wrong one. A
    number per summary makes a short answer visibly short instead.
    """

    summaries: list[ChunkSummary] = Field(...)

    # Shown to the model as the shape to imitate. See schema_instruction.
    model_config = {
        "json_schema_extra": {
            "example": {
                "summaries": [
                    {"num": 1, "summary": "EXAMPLE ONLY - your summary of excerpt 1"},
                    {"num": 2, "summary": "EXAMPLE ONLY - your summary of excerpt 2"},
                ]
            }
        }
    }
