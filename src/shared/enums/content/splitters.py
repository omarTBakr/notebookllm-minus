"""Which text splitter a file extension gets.

``Language`` here is langchain's splitter-grammar enum, not this project's
:class:`enums.Language` locale enum — the two share a name and nothing else,
which is why this table sits in its own module rather than beside either.
"""

from langchain_text_splitters import Language  # ty: ignore[unresolved-import]

# Extensions with their own structure-aware separator list. Anything not
# named here falls through to the plain-prose splitter — in particular .txt
# and .pdf.
LANGUAGE_SPLITTERS: dict[str, Language] = {
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
}
