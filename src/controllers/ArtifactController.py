"""What turning a batch of summaries into study material has in common.

Flashcards and quizzes differ in three things: the schema they ask the model
to fill, the prompt that asks for it, and the field the items come back under.
Everything else -- numbering the summaries, calling the model, translating the
answer back into real citations, refusing what cannot be traced -- is identical,
and lived as one function with a `kind` argument and a lookup table before this.

So the shared work is here once and each kind is a subclass declaring its three
differences. A third Studio tile with a generator behind it is a subclass and a
prompt, not another branch in a function that already has two.

Deliberately *not* holding the batching loop or the database: those belong to
the task, which owns the streaming and the resumability. A controller is given
a batch of summarised chunks and returns items.
"""

from enums import ArtifactKind
from utils import get_logger

from .StructuredController import generate_structured

logger = get_logger(__name__)

#: Items asked for per batch of summaries. Ten summaries rarely support more
#: than a handful of good questions, and asking for more is how a generator
#: starts padding.
ITEMS_PER_BATCH = 3


class ArtifactController:
    """Base: a batch of summarised chunks in, citable items out."""

    #: The Pydantic model the answer must fill. Its list field is required and
    #: it carries a worked example -- see models/db_schema/artifact.py for why
    #: both of those matter more than they look.
    schema: type = None

    #: Key in the `studio` prompt group, per locale.
    prompt_key: str = ""

    #: Field on `schema` holding the items.
    field: str = ""

    #: Which tile this is, for logs and for the registry.
    kind: ArtifactKind = None

    async def generate(self, client, parser, batch) -> list[dict]:
        """Turn a batch of summarised chunks into items that cite real pages.

        Summaries are numbered **1..N within this batch**, not by
        `chunk_order`. That is not cosmetic: `chunk_order` counts within one
        *document*, so a notebook holding two files has two chunk 5s, and a
        batch spanning both would offer the model the same number twice with no
        way to tell which was meant -- and no way to resolve the answer
        afterwards either.

        The batch-local number is unambiguous, matches what the prompt asks for
        ("the number of the summary it came from"), and is translated back here
        into the real chunk_order and the asset it belongs to. The model never
        sees an asset id and is never asked for one.
        """
        # 1-based, so it reads as "Summary 1" rather than "Summary 0" and
        # matches the citation numbering the chat answers already use.
        numbered = list(enumerate(batch, start=1))

        summaries = "\n\n".join(
            parser.get("studio", "summary_prompt", {"num": num, "content": chunk.summary}) for num, chunk in numbered
        )

        prompt = parser.get(
            "studio",
            self.prompt_key,
            {"summaries": summaries, "count": ITEMS_PER_BATCH},
        )

        result = await generate_structured(client, prompt, self.schema)
        items = [item.model_dump() for item in getattr(result, self.field)]

        return self._make_citable(items, dict(numbered), len(batch))

    def _make_citable(self, items: list[dict], by_number: dict, batch_size: int) -> list[dict]:
        """Attach the real chunk and asset, dropping what cannot be placed."""
        citable = []

        for item in items:
            chunk = by_number.get(item.get("chunk_order"))

            # A number outside this batch cannot be resolved to anything.
            # Dropping the item is the only honest option: a citation that
            # opens the wrong page confidently is worse than an item with no
            # citation at all.
            if chunk is None:
                continue

            item["chunk_order"] = chunk.chunk_order
            item["asset_id"] = chunk.asset_id
            citable.append(item)

        # Said out loud, because a silent drop is indistinguishable from a
        # model that simply had nothing to say -- and telling those apart is
        # the difference between "this batch was thin" and "every item was
        # thrown away and the deck is half the size it should be". Diagnosing
        # that took a probe script; it should have taken reading the log.
        dropped = len(items) - len(citable)

        if dropped:
            logger.warning(
                "Dropped %d of %d generated %s item(s): cited a summary outside the batch of %d",
                dropped,
                len(items),
                self.kind.value,
                batch_size,
            )

        if not items:
            logger.warning(
                "Model returned no %s items for a batch of %d summaries",
                self.kind.value,
                batch_size,
            )

        return citable
