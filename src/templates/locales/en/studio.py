"""English prompts for generated study material.

One group for all of Studio rather than one per tile: the three prompts here
are steps of a single pipeline — summarise a batch, then turn summaries into
cards or questions — and splitting them would separate things that are only
ever read together.

The pipeline exists because a notebook does not fit in a context window. A
222-page book is ~470k characters; the same book as one-line summaries is ~35k
tokens. So the model is given summaries and never the raw text, which is why
every prompt below insists on carrying `chunk_order` through: it is the only
thread back to a real page, and without it a generated card cannot be checked
against the document it claims to come from.
"""

# --- step one: a chunk becomes a line ------------------------------------------

# Numbered rather than fed one at a time: ten chunks per call turns 694 model
# calls into 70. The numbering is what lets the answer be matched back to the
# chunk it describes, so it has to survive into the output.
summarise_prompt = "\n".join(
    [
        "Below are numbered excerpts from a document.",
        "",
        "For each excerpt, write one sentence capturing what it is about — the",
        "specific claim, event, definition or argument it contains, not a",
        "description of the excerpt itself.",
        "",
        'Write "This passage discusses X" as "X", and keep names, dates and',
        "numbers: they are what makes a summary usable for writing questions",
        "later. If an excerpt is a heading, a table of contents or a page of",
        "front matter with no substance, say so in three words rather than",
        "inventing content for it.",
        "",
        "Write the summaries in English, whatever language the excerpts are in.",
        "",
        "Return one entry per excerpt, each carrying the number of the excerpt",
        "it describes. The number is how a summary is matched back to its page:",
        "an entry with the wrong number describes the wrong passage, and every",
        "question later built from it cites the wrong place. If you genuinely",
        "cannot summarise an excerpt, leave it out rather than guessing — a",
        "missing summary is retried, a mismatched one is not.",
        "",
        "{excerpts}",
    ]
)

# One excerpt inside that prompt. `num` is the position within this batch
# (1..N), not the chunk_order: chunk_order counts within one *document*, so a
# batch spanning two files would show the model the same number twice. The
# batch-local number is unambiguous and is translated back to the real
# chunk_order in `_summarise`.
excerpt_prompt = "\n".join(
    [
        "### Excerpt {num}",
        "{content}",
    ]
)


# --- step two: lines become cards ----------------------------------------------

flashcards_prompt = "\n".join(
    [
        "Below are numbered summaries of passages from one document.",
        "",
        "Write flashcards that test recall of what these passages actually say.",
        "",
        "A good card asks about one fact and has an answer of a few words to a",
        "sentence. Prefer specifics — a name, a date, a definition, a cause —",
        "over general questions like 'what is this chapter about', which cannot",
        "be marked right or wrong.",
        "",
        "Rules:",
        "- Every card must set chunk_order to the number of the summary it came",
        "  from. This is how a card is traced back to its page; a card with the",
        "  wrong number is worse than no card.",
        "- Do not write a card for a summary that says the passage is a heading,",
        "  a contents page or otherwise has no content.",
        "- Do not invent anything that is not in the summaries. If they support",
        "  fewer cards than asked for, return fewer.",
        "- Write the cards in English, whatever language the summaries are in.",
        "  This file is chosen by the notebook's language setting, so English",
        "  is what the reader asked for. Translate the substance; do not",
        "  reproduce the source wording in its own language.",
        "",
        "Write at most {count} cards.",
        "",
        "{summaries}",
    ]
)


# --- step two, the other kind: lines become questions ---------------------------

# Distractors are the whole difficulty here. A multiple-choice question is only
# as good as its wrong answers: if they are obviously wrong the question tests
# nothing, and if they are accidentally right it is unanswerable. This is where
# generated quizzes are usually visibly bad, so the prompt spends most of its
# length on it.
quiz_prompt = "\n".join(
    [
        "Below are numbered summaries of passages from one document.",
        "",
        "Write multiple-choice questions testing understanding of what these",
        "passages say. Each question has exactly four options, of which exactly",
        "one is correct.",
        "",
        "The wrong options are the hard part, and most of the work:",
        "- Each must be plausible to someone who has not read the passage —",
        "  the same kind of thing as the right answer. If the answer is a year,",
        "  the others are years; if it is a place, the others are places.",
        "- Each must be clearly wrong to someone who has read it. Never write an",
        "  option that is arguably also correct.",
        "- Every option must be a possible answer. Never offer the question",
        "  back as one of its own options, and never end an option with a",
        "  question mark — an option that is itself a question cannot answer one.",
        "- Do not use 'all of the above', 'none of the above', or an option that",
        "  is obviously absurd. Both make the answer findable without knowing it.",
        "- Do not make the correct option consistently the longest or the most",
        "  detailed. That is a tell, and it is how these quizzes are usually",
        "  beaten without reading anything.",
        "",
        "Rules:",
        "- answer_index is the 0-based position of the correct option.",
        "- Every question must set chunk_order to the number of the summary it",
        "  came from, so it can be traced back to its page.",
        "- Do not write a question for a summary that says the passage is a",
        "  heading or a contents page.",
        "- Do not invent anything not in the summaries. Fewer good questions are",
        "  better than filling a quota.",
        "- Write the questions in English, whatever language the summaries are in.",
        "  This file is chosen by the notebook's language setting, so English",
        "  is what the reader asked for. Translate the substance; do not",
        "  reproduce the source wording in its own language.",
        "",
        "Write at most {count} questions.",
        "",
        "{summaries}",
    ]
)

# One summary inside either prompt above.
summary_prompt = "\n".join(
    [
        "### Summary {num}",
        "{content}",
    ]
)
