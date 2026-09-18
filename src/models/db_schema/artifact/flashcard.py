"""One flashcard, and the set a batch of them arrives in.

`Flashcard` is the contract with the model: it is handed to
`generate_structured` as a JSON Schema and validates what comes back, and its
field names appear in the prompts, so renaming one changes what the model is
asked for.
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


class Flashcard(BaseModel):
    """One card. The schema a model is asked to fill, and the row's payload."""

    front: str = Field(..., min_length=1, max_length=500)
    back: str = Field(..., min_length=1, max_length=2000)

    # Which chunk this came from, so the card can be traced to a page through
    # the existing chunk-locate route. ge=0 because chunk_order is a position,
    # and a negative one would cite a page that cannot exist -- worth rejecting
    # at validation rather than discovering when a citation is clicked.
    #
    # Not sufficient on its own: chunk_order counts *within one document*, so a
    # notebook holding two files has two chunk 5s. The generating task attaches
    # the asset id after validation, from the chunk the summary came from --
    # the model has never seen an asset id and is not asked for one.
    chunk_order: int = Field(..., ge=0)


class FlashcardSet(BaseModel):
    """What one batch of summaries is expected to yield."""

    cards: list[Flashcard] = Field(...)

    model_config = {
        "json_schema_extra": {
            "example": {
                "cards": [
                    {
                        "front": "EXAMPLE ONLY - your question here",
                        "back": "EXAMPLE ONLY - your answer here",
                        "chunk_order": 1,
                    }
                ]
            }
        }
    }
