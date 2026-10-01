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
        "For each excerpt, write one or two sentences stating what it says — the",
        "specific claim, event, definition or argument it contains, not a",
        "description of the excerpt itself.",
        "",
        "These summaries are all that is kept. Questions and flashcards are",
        "written later from the summaries alone, by someone who never sees the",
        "excerpt, and every answer they mark as correct is only as true as your",
        "sentence. So:",
        "",
        '- Name the subject. Write "The Treaty of Westphalia ended the Thirty',
        '  Years\' War in 1648", never "This treaty ended the war that year".',
        '  A summary that says "it", "this method" or "the author" is',
        "  useless once it is separated from its excerpt. (That example is about",
        "  an unrelated subject on purpose: never reuse its wording.)",
        "- Say only what the excerpt says, and say it exactly. Keep negations,",
        '  conditions and hedges: "reduces" is not "eliminates", "can" is',
        '  not "always", and "allegedly" or "according to X" stays in.',
        "  Never reverse a claim, and never add a fact the excerpt does not",
        "  state, however well you know the subject.",
        "- Keep names, dates and numbers: they are what makes a summary usable",
        "  for writing questions later.",
        '- Write "This passage discusses X" as "X".',
        "- If an excerpt is a heading, a table of contents, a list of references",
        "  or a page of front matter with no substance, say so in three words",
        "  rather than inventing content for it.",
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
# chunk_order in `summarise_chunks`.
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

# Two things go wrong with generated quizzes, and this prompt is built around
# both. The correct answer is wrong: a small model asked for four options and
# the position of the right one lost track of which was which, and marked
# "RAG is used for data compression" correct. So the model no longer gives a
# position -- it writes the correct answer as text, apart from the wrong ones,
# and code shuffles them and finds the index (QuizQuestion.as_item). And the
# distractors are bad: obviously wrong tests nothing, accidentally right makes
# the question unanswerable.
quiz_prompt = "\n".join(
    [
        "Below are numbered summaries of passages from one document.",
        "",
        "Write multiple-choice questions testing understanding of what these",
        "passages say. For each question give the correct answer and three wrong",
        "answers, separately.",
        "",
        "Work through each question in this order:",
        "1. Choose one summary and one specific fact it states.",
        "2. Write the question so that fact alone answers it. Name the subject:",
        '   "Which war did the Treaty of Westphalia end?", never "What did this',
        '   treaty end?". The reader sees the question without the passage.',
        "   (The example is about an unrelated subject on purpose: take your",
        "   wording from the summary, never from these instructions.)",
        "3. Write correct_answer: the answer as that summary states it. It must",
        "   be true according to the summary. If you cannot point to the words",
        "   in the summary that make it true, drop the question.",
        "4. Write three wrong_answers. Each must be false according to the",
        "   summary. Check each one against it: if the summary supports it, or",
        "   it says the same thing as the correct answer in other words, replace",
        "   it.",
        "",
        "What makes the wrong answers good:",
        "- Each must be plausible to someone who has not read the passage —",
        "  the same kind of thing as the right answer. If the answer is a year,",
        "  the others are years; if it is a place, the others are places.",
        "- Each must differ from the correct answer in substance, not in a word",
        "  or two at the end. Four options that repeat the question's wording",
        "  and change only their last few words test nothing.",
        "- Every answer must be a possible answer. Never offer the question",
        "  back as an answer, and never end an answer with a question mark.",
        "- Do not use 'all of the above', 'none of the above', or an answer that",
        "  is obviously absurd. Both make the right one findable without",
        "  knowing it.",
        "- Keep the four about the same length and detail. A correct answer that",
        "  is always the longest is a tell.",
        "",
        "Rules:",
        "- correct_answer holds the text of the one correct answer. wrong_answers",
        "  holds exactly three texts, all of them false. Do not letter or number",
        "  the answers and do not say which position is correct: the four are",
        "  shuffled after you answer.",
        "- Every question must set chunk_order to the number of the summary it",
        "  came from, so it can be traced back to its page.",
        "- Do not write a question for a summary that says the passage is a",
        "  heading, a contents page or a list of references -- not even one",
        "  asking what the section contains.",
        "- Each question tests a different fact. Do not ask the same question",
        "  twice in different words.",
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


# --- step two, mind map: lines become topics -----------------------------------

mindmap_prompt = "\n".join(
    [
        "Below are numbered summaries of passages from one document.",
        "",
        "Pick out the main topics these passages cover, for a mind map of the",
        "document. A topic is a short noun phrase of two to six words -- a",
        "concept, person, event, method or argument -- not a sentence.",
        "",
        "For each topic give:",
        "- topic: the short name that goes on the map.",
        "- detail: one sentence saying what the passage says about it.",
        "- chunk_order: the number of the summary it came from. This is how a",
        "  node is traced back to its page; a wrong number is worse than none.",
        "",
        "Rules:",
        "- Skip summaries that say the passage is a heading, a contents page or",
        "  otherwise has no content.",
        "- One topic per idea. Do not list the same idea twice under different",
        "  wording.",
        "- Do not invent anything that is not in the summaries.",
        "- Write in English, whatever language the summaries are in.",
        "",
        "Write at most {count} topics.",
        "",
        "{summaries}",
    ]
)

# Every topic the batches produced, shown at once so the branches can be chosen
# for the whole document rather than batch by batch.
mindmap_outline_prompt = "\n".join(
    [
        "Below is a numbered list of topics taken from one document.",
        "",
        "Group them into the main branches of a mind map of that document.",
        "",
        "Rules:",
        "- Use between 3 and 8 branches. Each title is a short phrase of one to",
        "  four words naming what its topics have in common.",
        "- Put every topic in exactly one branch, by its number.",
        "- Branch titles must all be different.",
        "- Order the branches the way a reader would meet them in the document.",
        "- Write the titles in English.",
        "",
        "{topics}",
    ]
)

# One line inside the outline prompt above.
mindmap_topic_prompt = "{num}. {topic}"

# The branch for topics the outline left out. Plain text, not a prompt.
mindmap_other_branch = "Other"
