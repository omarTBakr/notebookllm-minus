"""English prompts for describing a picture found in a document.

The caption has two readers: the reranker, which scores it against a user's
question exactly like a passage of text, and a chat model without vision,
which reads it in place of the picture. Both want what the picture *says*
(the numbers on a chart, the labels on a diagram, the words on a sign), not
an impression of it, so the prompt asks for content and transcription first.
"""

# Version 1.
caption_prompt = "\n".join(
    [
        "Describe this image from a document so that someone who cannot see it",
        "could answer questions about it.",
        "",
        "- Say what kind of image it is (chart, diagram, table, map, photo,",
        "  screenshot, scanned page...) and what it shows, in 1 to 3 sentences.",
        "- Copy any text, labels, numbers, axis titles or legend entries exactly",
        "  as written, in their original language.",
        "- For a chart, state the main values or trend it shows.",
        "- Describe only what is visible. Do not guess at context that is not",
        "  in the image.",
        "",
        "{context}",
        "Write the description in {language}. Output the description only.",
    ]
)

# The text printed around the picture on its page, when there is any. It
# names things the picture alone cannot (whose revenue, which year).
caption_context = 'Text printed near the image on its page: "{nearby}"'

language_name = "English"
