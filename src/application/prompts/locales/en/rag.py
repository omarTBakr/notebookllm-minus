"""English prompts for grounded (document-backed) answering.

One file per feature: editing how retrieval is framed never means scrolling
past the plain-chat prompts.
"""

# The balance this prompt has to strike: refuse to invent *facts* that are not
# in the documents, while still doing real work *on* them. An earlier version
# said only "answer using ONLY the documents", which made the model refuse
# "what do you think of this CV?" — there is no sentence in a CV stating an
# opinion of it, so it decided the documents did not contain the answer.
system_prompt = "\n".join(
    [
        "You are a careful research assistant working with the user's documents.",
        "The documents below are your source material.",
        "",
        "What to do:",
        "- Answer questions about the documents using their contents.",
        "- When asked to summarise, review, assess, critique, compare or draw",
        "  conclusions, do that work using the documents. These are valid requests",
        "  even though the documents contain no sentence that states the answer",
        "  outright — reason from what they say.",
        "- Cite the documents you drew on by number, like [1] or [2].",
        "",
        "What not to do:",
        "- Do not state facts that are not supported by the documents -- except for",
        '  anything in a "What you know about this user" section below, if one is',
        "  present. That is separate, trusted context about the user themselves,",
        "  not a document, and is not something to cite by number.",
        "- Only if the documents are genuinely unrelated to what was asked, say so",
        "  plainly. Do not say it merely because the answer is not stated verbatim.",
        "- Never cite a document number that was not provided to you.",
        "",
        "Answer in the same language the user asked in, and be concise.",
    ]
)

# One retrieved chunk. `num` is 1-based so it matches the [1] the model cites.
document_prompt = "\n".join(
    [
        "## Document {num}",
        "Source: {source}",
        "{content}",
    ]
)

# Drives QueryDecomposer.decompose_query, ahead of the single search() call
# ChatService.answer_stream used to make. Explicitly told not to invent a
# split that isn't there: a model asked for "queries" tends to pad out a
# simple question into several near-duplicates, which just wastes search
# calls and clutters the merged hit list with near-identical passages.
decompose_prompt = "\n".join(
    [
        "A user asked the question below in a chat backed by their own documents.",
        "Rewrite it as 1 to 3 short, independent search queries that will retrieve",
        "the passages needed to answer it.",
        "",
        "If the question is genuinely compound -- it asks for more than one thing",
        "-- give one query per thing, each self-contained (resolve pronouns like",
        "'it' or 'they' using the rest of the question).",
        "",
        "If the question is not compound, return exactly one query: a rephrasing",
        "of it that is likely to retrieve better than the question's own wording,",
        "not a fabricated multi-part split. Most questions belong here -- do not",
        "invent sub-parts that are not actually in the question.",
        "",
        "Question:",
        "{question}",
    ]
)

# Sits between the documents (and, when there are any, the memory block) and
# the question. "The material above" rather than "the documents above" is
# deliberate: this same footer follows a memory_context_prompt block when one
# is present, and wording this as document-only would repeat the same
# document-vs-memory conflict system_prompt's own "what not to do" section
# exists to prevent.
footer_prompt = "\n".join(
    [
        "---",
        "Using the material above as your source, respond to the following.",
        "Analysis, summary and judgement are welcome as long as they follow from it.",
        "Say it isn't covered only if none of it is relevant.",
        "",
        "Request: {question}",
        "Response:",
    ]
)
