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

# Drives QueryPlanner.plan_query, the first step of every grounded answer.
#
# Version 2 (Langfuse: en/rag.plan_prompt v2). v1 sent greetings and thanks to
# retrieval and invented sub-queries the user never asked for (seen in the
# live end-to-end run with llama3.1:8b). v2 defines chitchat by "asks for no
# information", gives one example per decision (deliberately off-topic), and
# forbids extra or repeated parts.
# Three jobs in one call: classify the intent, rewrite the question so it
# stands alone without the chat history, and give the retrieval queries.
# Told explicitly not to invent a split: a model asked for "queries" tends to
# pad a simple question into near-duplicates, which costs a sub-answer each.
plan_prompt = "\n".join(
    [
        "You plan how to answer a message in a chat backed by the user's own documents.",
        "You do not answer it. You only classify it and write search queries.",
        "",
        "Recent conversation (oldest first):",
        "{history}",
        "",
        "New message:",
        "{question}",
        "",
        "Step 1 - intent. Choose exactly one:",
        "",
        '- "chitchat": the message asks for no information at all. Greetings',
        "  (hi, hello, good morning), thanks (thanks, thank you, great job), goodbyes,",
        "  apologies, reactions (ok, cool, got it), or questions about you, the",
        "  assistant (who are you, what can you do). A message that greets or thanks",
        "  AND also asks something is not chitchat: classify the question in it.",
        '- "simple": it asks for one piece of information, however long the answer',
        "  may be. Explanations, summaries and definitions are simple.",
        '- "complex": it asks for two or more DIFFERENT things that need separate',
        "  lookups, joined by and, also, as well as, or a comparison (A versus B).",
        "",
        "Examples (the topics are illustrations only, never reuse them):",
        '  "Hello!" -> chitchat',
        '  "Thanks, that was helpful" -> chitchat',
        '  "What can you do?" -> chitchat',
        '  "Thanks! And when did the bakery open?" -> simple',
        '  "Explain how the irrigation system works" -> simple',
        '  "How old is the clock tower, and who painted the mural?" -> complex',
        '  "Compare the winter and summer timetables" -> complex',
        "",
        "When unsure between chitchat and simple, choose simple. When unsure between",
        "simple and complex, choose simple.",
        "",
        "Step 2 - standalone_query: rewrite the new message so it is fully understood",
        "without the conversation. Replace pronouns and references (it, they, that",
        "one, the second) with what they refer to. Keep the user's language and",
        "meaning; add nothing they did not ask.",
        "",
        "Step 3 - sub_queries: short search queries, in the user's language.",
        "- chitchat: exactly one, the standalone message itself.",
        "- simple: exactly one, worded to find the passage that answers it.",
        "- complex: one per part the message actually asks for, at most",
        "  {max_sub_queries}. Never add a part the user did not ask about, and never",
        "  repeat a part in different words.",
    ]
)

# Drives one sub-answer on the complex path: a narrow question answered from
# the passages retrieved for it alone. The numbers on those passages are the
# global ones the final answer will cite, so "[7]" here stays "[7]" there.
subanswer_prompt = "\n".join(
    [
        "---",
        "Using only the documents above, answer this one part of a larger question.",
        "Cite every fact with the document number in brackets, like [3], using exactly",
        "the numbers shown above. Be brief: a few sentences or a short list. If the",
        "documents do not cover it, say so in one sentence.",
        "",
        "Part: {question}",
        "Answer:",
    ]
)

# The final call on the complex path. It never sees the passages, only the
# partial answers written from them, so its citations have to be carried
# over rather than invented.
synthesis_prompt = "\n".join(
    [
        "A question was split into parts, and each part was researched separately in",
        "the user's documents. The findings are below, with document numbers in brackets.",
        "",
        "{findings}",
        "",
        "---",
        "Write one answer to the original question from these findings.",
        "- Combine them; do not just list them part by part unless that reads best.",
        "- Keep the bracketed document numbers on every fact you use, exactly as they",
        "  appear above. Never add a number that is not in the findings.",
        "- Where a finding says the documents do not cover something, say so.",
        "",
        "Original question: {question}",
        "Answer:",
    ]
)

# What a part with no passages, or a failed sub-answer, reports to synthesis.
part_not_covered = "The documents do not cover this part."

# One finding inside synthesis_prompt.
finding_prompt = "\n".join(
    [
        "### Part {num}: {question}",
        "{answer}",
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
