"""Everything done to the text itself, once a loader has produced it.

Kept apart from ProcessController, which is about getting bytes off disk and
turning them into Documents. Nothing here touches the filesystem or knows what
kind of file the text came out of, which is what lets the sanitising and the
size guard be exercised on a plain string.

Two jobs, in the order the ingest path needs them:

  sanitize   make the extracted text safe to store
  split      cut it into chunks no larger than the configured size
"""

from langchain_core.documents import Document  # ty: ignore[unresolved-import]
from langchain_text_splitters import (  # ty: ignore[unresolved-import]
    NLTKTextSplitter,
    RecursiveCharacterTextSplitter,
)

from enums import ProcessStatus
from exceptions import ChunkingError
from .BaseController import BaseController

# Whether this process has confirmed the punkt model is on disk.
_PUNKT_READY = False


def strip_nulls(value):
    """Remove NUL bytes from a string, walking nested metadata.

    PDF extraction produces them: a glyph whose ToUnicode CMap entry is broken
    or missing decodes to \\x00 rather than raising, so a file with one damaged
    font yields text that looks fine and carries NULs through it. Files written
    by one tool and edited by another are the usual source.

    PostgreSQL rejects \\x00 in text *and* in jsonb outright — it stores strings
    NUL-terminated, so this is not an encoding question and no client setting
    changes it. One bad glyph anywhere therefore failed the whole ingest INSERT
    (CharacterNotInRepertoireError), taking every chunk of the document with
    it. Mongo stores them happily, which is why this only ever showed up on the
    Postgres backend.

    Stripping rather than replacing: the byte carries no meaning here — it is
    the absence of a character the font could not name.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {strip_nulls(k): strip_nulls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nulls(v) for v in value]
    return value


class TextProcessingController(BaseController):
    """Sanitising and chunking, for text that is already in memory."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # --- sanitising -----------------------------------------------------------

    def sanitize(self, docs: list[Document], source: str = "") -> list[Document]:
        """Strip anything the stores refuse, from content and metadata alike.

        Done once, here, rather than at either repository: this is the single
        point every ingest path passes through, so both backends get clean text
        and neither has to know the loaders can emit NULs at all.

        Edits the documents in place and returns them, so it reads as a stage
        in a pipeline.
        """
        damaged = 0

        for doc in docs:
            if "\x00" in doc.page_content:
                damaged += 1
                doc.page_content = doc.page_content.replace("\x00", "")
            doc.metadata = strip_nulls(doc.metadata)

        if damaged:
            self.logger.warning(
                "Removed NUL bytes from %d of %d page(s) of %s — the file has a "
                "damaged font encoding, so some glyphs did not decode",
                damaged,
                len(docs),
                source or "the document",
            )

        return docs

    # --- splitting ------------------------------------------------------------

    def _ensure_punkt(self) -> None:
        """Put NLTK's sentence model on disk if it is not already there.

        NLTKTextSplitter tokenises sentences with punkt, which ships as data
        rather than as code: nltk does not bundle it and langchain does not
        fetch it. On a machine that has never downloaded it every split raises
        LookupError, so a working laptop proves nothing about a fresh
        container. Done once per process, and only when a split is imminent.
        """
        global _PUNKT_READY
        if _PUNKT_READY:
            return

        import nltk  # local: only this splitter needs it

        try:
            nltk.data.find("tokenizers/punkt_tab")
        except LookupError:
            self.logger.info("Downloading NLTK punkt model (first run only)")
            nltk.download("punkt_tab", quiet=True)

        _PUNKT_READY = True

    def get_splitter(self) -> NLTKTextSplitter:
        """The primary splitter: cuts on sentence boundaries, not characters."""
        self._ensure_punkt()
        return NLTKTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )

    def get_size_guard(self) -> RecursiveCharacterTextSplitter:
        """The ceiling the sentence splitter does not enforce."""
        return RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )

    def enforce_size(self, chunks: list[Document]) -> list[Document]:
        """Break up whatever the sentence splitter left over the limit.

        NLTKTextSplitter splits *between* sentences and never within one, so a
        passage punkt reads as a single sentence comes back whole however long
        it is — and text extracted from a PDF frequently carries no sentence
        punctuation at all, which turns a whole document into one chunk.
        Measured on this project's own corpus, that produced a 213,000-char
        chunk where the limit was 1000: retrieval loses all granularity and the
        embedding model silently truncates the rest.

        Chunks already within the limit pass through untouched, so on clean
        prose this is a no-op and punkt's sentence boundaries survive intact.
        """
        oversized = sum(1 for c in chunks if len(c.page_content) > self.chunk_size)
        if not oversized:
            return chunks

        guard = self.get_size_guard()
        out: list[Document] = []

        for chunk in chunks:
            if len(chunk.page_content) > self.chunk_size:
                # split_documents carries the metadata onto each new piece.
                out.extend(guard.split_documents([chunk]))
            else:
                out.append(chunk)

        self.logger.info(
            "Size guard: re-split %d oversized chunk(s), %d -> %d total (limit=%s)",
            oversized,
            len(chunks),
            len(out),
            self.chunk_size,
        )
        return out

    def split(self, docs: list[Document]) -> list[Document]:
        """Sentence-split, then enforce the size ceiling."""
        try:
            chunks = self.enforce_size(self.get_splitter().split_documents(docs))
        except Exception as exc:
            raise ChunkingError(
                f"{ProcessStatus.CHUNKING_FAILED.value}: chunk_size={self.chunk_size}, "
                f"overlap={self.chunk_overlap}"
            ) from exc

        self.logger.info(
            "Split %d document(s) into %d chunk(s) (chunk_size=%s, overlap=%s)",
            len(docs),
            len(chunks),
            self.chunk_size,
            self.chunk_overlap,
        )
        return chunks
