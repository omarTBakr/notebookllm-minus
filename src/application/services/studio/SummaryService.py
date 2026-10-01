"""One-line summaries of chunks, a batch per model call.

Shared by the two places that summarise: ingestion, which summarises every
chunk of a new document right after it is stored, and Studio generation, which
summarises whatever is still missing before it writes items. Both write the
result onto the chunk, so whichever runs first saves the other the call.
"""

from data.models import SummarySet

from ..llm.structured_generation import generate_structured

#: Chunks summarised per model call. Ten turns 694 calls into 70, which is the
#: difference between a job that finishes and one nobody waits for.
SUMMARY_BATCH = 10


async def summarise_chunks(client, parser, batch) -> dict[str, str]:
    """One model call for a batch of chunks, returning summaries by row id.

    Structured, not prose split on newlines. Splitting was the obvious thing
    and it was wrong: a small model answering a ten-excerpt batch with a single
    line had that line assigned to every chunk in the batch, so nine of them
    carried a summary describing a different passage -- and every card built
    from those cited the wrong chunk. Found by running it, not by testing it;
    the fake in the unit tests politely returned one line per excerpt.

    Numbered 1..N within the batch, matching `ArtifactService.generate`,
    so a summary is tied to a chunk by a number the model was actually shown.
    A chunk the model skips is simply left unsummarised: it has no summary
    rather than someone else's, and the next run picks it up.
    """
    numbered = list(enumerate(batch, start=1))

    excerpts = "\n\n".join(
        parser.get(
            "studio",
            "excerpt_prompt",
            {"num": num, "content": chunk.chunk_content[:4000]},
        )
        for num, chunk in numbered
    )

    prompt = parser.get("studio", "summarise_prompt", {"excerpts": excerpts})
    # Partial is safe here: a chunk left without a summary is retried by the
    # next run, which is exactly what happens when the model skips one.
    result = await generate_structured(client, prompt, SummarySet, salvage=True)

    by_number = dict(numbered)
    summaries: dict[str, str] = {}

    for entry in result.summaries:
        chunk = by_number.get(entry.num)

        if chunk is not None:
            summaries[str(chunk.id)] = entry.summary

    return summaries
