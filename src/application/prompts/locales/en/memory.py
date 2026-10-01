"""English prompts for `/memory`: extraction, and folding facts into an answer.

One prompt, not a pipeline: unlike Studio's summarise-then-generate steps, this
is a single message short enough to read in one call. See
`application/services/memory/MemoryService.py`.
"""

# The category list ("name, age, location, ...") and the explicit line about
# preferences both earn their place from measurement, not taste. The first
# version of this prompt listed only "diet, job, preferences, or background"
# and llama-3.2-11b-vision-instruct (this deployment's GENERATION_MODEL_ID)
# read that as an exhaustive list: "my age is 35" and "my full name is Omar
# Ayman Bakr" both came back `{"facts": []}` -- a clean, schema-valid, wrong
# answer that generate_structured's retry-on-*invalid* loop never catches,
# because nothing about it fails validation. Broadening the category list
# fixed those two, but "I like turtles" still came back empty even with
# "preferences" already listed -- the model's own words, when asked to
# explain itself, were that a stated like is "too trivial" to count as a
# durable fact. Only an explicit line overriding that judgement ("treat
# 'I like turtles' the same as a stated name or job") got it through. Tested
# against this exact model before and after; do not narrow this back down
# without re-testing the same way.
extract_prompt = "\n".join(
    [
        "Below is a message a user sent after typing /memory in a chat, asking",
        "you to remember something about them.",
        "",
        "Pull out the durable facts about the user it states or clearly implies",
        "-- things like their name, age, location, job, diet, family, hobbies,",
        "or background. A stated like, dislike, or preference counts as a fact",
        'too -- treat "I like turtles" the same as a stated name or job, not as',
        "too trivial to record. Do not treat the categories above as exhaustive:",
        "any durable personal fact counts.",
        "",
        "Return them as a list of key/description pairs. Each key is a short",
        "snake_case label ('diet', 'job_title', 'likes'); each description is",
        "one self-contained sentence stating the fact **in the third person**",
        '("The user is 35", never "I am 35") so it still makes sense read on',
        "its own later, outside this conversation.",
        "",
        "Do not invent facts the message does not support. A one-off request",
        "('summarize this') or a question is not a fact about the user -- but a",
        "statement about them, however small, is. If the message truly states",
        "no fact about the user, return an empty list rather than stretching",
        "for one.",
        "",
        "Message:",
        "{text}",
    ]
)

# One stored fact, folded into an answer prompt (ChatService.build_prompt).
# Deliberately just a bullet -- these are never numbered or cited the way
# document_prompt's chunks are: asking a model to track two independent
# citation schemes in one prompt is exactly the kind of thing that has
# already gone wrong elsewhere in this codebase (see the batch-local
# numbering fix in ArtifactService / TextCorrectionService).
memory_fact_prompt = "- {description}"

# Wraps the joined memory_fact_prompt lines. Told explicitly not to mention
# this context exists -- without that, small models tend to open with
# "Since you mentioned you're vegetarian, ..." on every single answer, which
# reads as the assistant narrating its own retrieval rather than just being
# personal.
memory_context_prompt = "\n".join(
    [
        "What you know about this user, from things they've told you before:",
        "{facts}",
        "",
        "Use this naturally if it's relevant to their question. Don't mention",
        "that you were given this information, and ignore it entirely if it has",
        "nothing to do with what they're asking.",
    ]
)
