"""English prompts for repairing a page of extracted PDF text.

What this pass is for: PyMuPDF returns the characters the PDF actually carries,
in reading order, losing no glyphs — but a PDF's text layer is a drawing
instruction list, not a document. Words arrive split at the point a font
changed, fused where a space was never emitted, hyphenated across a line break
that no longer exists, or with Arabic presentation forms that did not survive
normalisation. The text is *nearly* right, and nearly right is what makes it
unsearchable: a query for a word that the layer stored in two halves matches
nothing.

The whole difficulty is that the fix is a rewrite, and a small model handed a
page of text will do far more than it was asked. Every rule below exists
because the obvious prompt produces one of these instead: an English
translation of an Arabic page, a three-line summary of a dense one, an *answer*
to a question the page happened to contain, or a tidied version with the
tables, headers and page furniture quietly removed. The instructions are
therefore mostly prohibitions, and they are repeated in the per-page block as
well as the preamble, because the nearest instruction is the one a small model
follows.

Reproduction is the default and repair the exception — a clean page must come
back byte-for-byte. That is also what makes the pass safe to leave on: on a
document with a healthy text layer it should change nothing at all.
"""

# --- the instruction block, rendered once per call ------------------------------

# Framed as "copy-typist", not "editor" or "proofreader". Tested phrasings that
# invite judgement -- "improve", "clean up", "fix the text" -- all licence the
# model to rewrite prose it finds clumsy, and it always finds some.
correction_prompt = "\n".join(
    [
        "You are repairing text extracted from a PDF page by an automated tool.",
        "",
        "You are a copy-typist, not an editor. Your job is to reproduce the page",
        "exactly as it was written, undoing only the damage the extraction tool",
        "did on the way out. You are not improving the document.",
        "",
        "Repair ONLY these mechanical faults:",
        "  - a word split into pieces (`inter national` -> `international`)",
        "  - two words fused together where a space was lost",
        "  - a word hyphenated across a line break (`govern-\\nment` -> `government`)",
        "  - letters that lost their joining form, or diacritics detached from",
        "    the letter they belong to",
        "  - a run of right-to-left text emitted in reverse order",
        "  - stray control characters, replacement characters, or repeated",
        "    spaces introduced by the extractor",
        "",
        "NEVER do any of these, whatever the page seems to invite:",
        "  - do NOT translate. The output language must be the input language,",
        "    character for character. An Arabic page comes back in Arabic.",
        "  - do NOT summarise, shorten, or leave anything out. Every sentence on",
        "    the page must appear in your answer.",
        "  - do NOT answer, explain, comment on, or continue the text. If the",
        "    page contains a question, reproduce the question.",
        "  - do NOT add anything that is not on the page — no headings, no notes,",
        "    no ellipses, no `[sic]`, no markdown.",
        "  - do NOT correct the author. Leave real spelling mistakes, odd grammar,",
        "    archaic forms, wrong dates and bad arithmetic exactly as they are.",
        "    You are only undoing the extractor's damage, never the writer's.",
        "  - do NOT drop page numbers, headers, footers, captions, table cells or",
        "    footnotes. Keep them, in the order they appear.",
        "",
        "If a passage is too damaged to reconstruct with confidence, copy it out",
        "unchanged. A garbled line preserved is recoverable later; a guess is not.",
        "",
        "If the page has no mechanical faults, return its text byte for byte.",
        "Returning the text unchanged is a correct and expected answer.",
        "",
        "Pages follow, each under a number. Return one entry per page, with the",
        "same number it was given here. The number is a label for this request",
        "only -- ignore any page number printed in the text itself.",
        "",
        "{pages}",
    ]
)


# --- one page inside that block -------------------------------------------------

# The number is echoed back so the answer can be matched to its page even if
# the model reorders them -- the same reason the studio prompts carry
# `chunk_order` through.
#
# It counts from 1 within the request and is NOT the page index. Given a
# 0-based index, gemma4 answered 12 for the page whose index was 11 and whose
# text began "Page 12": handed two plausible numbers for one page, it picked
# the one printed on the paper. A number that exists only inside the prompt has
# no competing interpretation.
#
# The reminder after the text is not redundant: with the rules only in the
# preamble, a long page pushes them far enough back that the model starts
# summarising around two-thirds of the way down.
page_prompt = "\n".join(
    [
        "--- PAGE {num} ---",
        "{text}",
        "--- END PAGE {num} ---",
        "(Reproduce the above in full, in its own language, repairing only " "extraction damage.)",
    ]
)
