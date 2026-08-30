"""Everything done to the text itself, once a loader has produced it.

Kept apart from ProcessController, which is about getting bytes off disk and
turning them into Documents. Nothing here touches the filesystem or knows what
kind of file the text came out of, which is what lets the sanitising and the
size guard be exercised on a plain string.

Two jobs, in the order the ingest path needs them:

  sanitize   make the extracted text safe to store
  split      cut it into chunks no larger than the configured size
"""

import re
import unicodedata

from langchain_core.documents import Document  # ty: ignore[unresolved-import]
from langchain_text_splitters import (  # ty: ignore[unresolved-import]
    Language,
    NLTKTextSplitter,
    RecursiveCharacterTextSplitter,
)

from enums import ProcessStatus
from exceptions import ChunkingError
from .BaseController import BaseController

# Whether this process has confirmed the punkt model is on disk.
_PUNKT_READY = False

# Directional formatting characters. PDF producers emit these to force the
# visual order of mixed-direction text; they carry no meaning once the text is
# a string, and an embedding model tokenises them as noise. A 37k-character
# sample of this project's own Arabic corpus contained 11,120 of them.
_BIDI_CONTROLS = dict.fromkeys(
    map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
)


def normalize_text(value: str) -> str:
    """Fold extracted text back to the codepoints a query will be written in.

    PDF extraction of Arabic (and other shaped scripts) yields *presentation
    forms* — U+FB50–FDFF and U+FE70–FEFF, the per-position glyph variants a
    font actually draws — rather than the standard letters in U+0600–06FF that
    anyone typing a question produces. They render identically and compare as
    entirely different characters.

    Measured on this project's corpus: 69.7% of Arabic letters were
    presentation forms, and normalising a chunk lifted its cosine similarity
    against a normally-typed query from 0.5495 to 0.6895 — the single largest
    retrieval improvement available here.

    NFKC is the compatibility normalisation that maps those forms back. The
    same pass drops bidi controls and collapses the runs of whitespace that
    removing them leaves behind.
    """
    if not value:
        return value

    return re.sub(r"[ \t\u00a0]{2,}", " ", unicodedata.normalize("NFKC", value).translate(_BIDI_CONTROLS))


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
        """Make extracted text safe to store *and* comparable to a query.

        Two jobs, both here because this is the single point every ingest path
        crosses. Stripping NULs is about what the stores will accept;
        normalising is about whether retrieval can work at all — see
        `normalize_text`. Doing it at extraction means the vectors are built
        from the normalised form, which is the only place it can matter.

        Edits the documents in place and returns them, so it reads as a stage
        in a pipeline.
        """
        damaged = 0
        reshaped = 0

        for doc in docs:
            if "\x00" in doc.page_content:
                damaged += 1
                doc.page_content = doc.page_content.replace("\x00", "")

            normalized = normalize_text(doc.page_content)
            if normalized != doc.page_content:
                reshaped += 1
                doc.page_content = normalized

            doc.metadata = strip_nulls(doc.metadata)

        if reshaped:
            self.logger.info(
                "Normalised %d of %d page(s) of %s (presentation forms, bidi "
                "controls) so the text matches how a query is typed",
                reshaped,
                len(docs),
                source or "the document",
            )

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

    # Extensions with their own structure-aware separator list. Anything not
    # named here falls through to the plain-prose splitter below — in
    # particular .txt and .pdf, which is unchanged from before this existed.
    _LANGUAGE_SPLITTERS = {
        ".md": Language.MARKDOWN,
        ".markdown": Language.MARKDOWN,
    }

    def get_splitter(self, extension: str | None = None) -> RecursiveCharacterTextSplitter:
        """The primary splitter: paragraph first, then line, sentence, word.

        Back from NLTKTextSplitter, which had two problems as a retrieval unit.
        It joins sentences with "\n\n", so the source's own paragraph structure
        is destroyed and every chunk looks like a list of sentences. And
        because its splits are whole sentences, LangChain's merge step —
        which pops leading splits `while total > chunk_overlap` — discards the
        entire carry-over whenever a trailing sentence is longer than the
        overlap. With the overlap this project uses that meant *no* overlap at
        all, and a fact spanning a chunk boundary became unretrievable.

        The separator list is the library default with ". " inserted, so a
        paragraph is preferred, then a line, then a sentence end, then a word.
        The final "" is kept deliberately: it is the only separator that can
        cut inside an unbroken run, and PDF extraction produces pages with no
        whitespace at all. Without it those pages come back as one chunk many
        times the limit — which is what `enforce_size` exists to catch.

        ``extension`` is an optional hint — this class still knows nothing
        about files, only the string a caller passes it. A recognised one
        (currently ``.md``) selects langchain's language-aware separator list
        instead, which prefers headings and fenced code before falling back to
        the same paragraph/line/sentence/word chain, `""` last resort included
        — so the no-whitespace-PDF case `enforce_size` guards against is
        exactly as covered for a markdown file that turns out to have none.
        """
        language = self._LANGUAGE_SPLITTERS.get((extension or "").lower())

        # Where this chunk starts in the page it came from — a PDF highlight
        # is computed from this offset, so it has to survive from here all
        # the way through enforce_size (which rebases it) to ProcessController.
        if language is not None:
            return RecursiveCharacterTextSplitter.from_language(
                language,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                add_start_index=True,
            )

        return RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
            add_start_index=True,
        )

    def _nltk_splitter(self) -> NLTKTextSplitter:
        """Sentence-boundary splitting. Not currently used — see get_splitter.

        Kept, with `_ensure_punkt`, for a return to it. Note that restoring it
        means restoring the overlap problem described above unless
        CHAT_CHUNK_OVERLAP is raised well past one sentence.
        """
        self._ensure_punkt()
        return NLTKTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )

    def get_size_guard(self) -> RecursiveCharacterTextSplitter:
        """The ceiling the sentence splitter does not enforce."""
        return RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            add_start_index=True,
        )

    def enforce_size(self, chunks: list[Document]) -> list[Document]:
        """Break up whatever the primary splitter left over the limit.

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
                # split_documents carries the metadata onto each new piece —
                # including start_index, which langchain recomputes relative
                # to *this chunk's own text*, not the page it was cut from.
                # Left alone, a highlight built from it would be computed
                # against the wrong string: plausible-looking rects, wrong
                # part of the page. Rebased here, once, onto the parent's own
                # offset — the only place both numbers are in hand together.
                parent_start = chunk.metadata.get("start_index")
                pieces = guard.split_documents([chunk])

                if parent_start is not None and parent_start >= 0:
                    for piece in pieces:
                        child_start = piece.metadata.get("start_index")
                        piece.metadata["start_index"] = (
                            parent_start + child_start
                            if child_start is not None and child_start >= 0
                            else -1
                        )

                out.extend(pieces)
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

    def split(self, docs: list[Document], extension: str | None = None) -> list[Document]:
        """Split, then enforce the size ceiling.

        ``extension`` picks the separator list — see `get_splitter`. The size
        guard itself stays language-agnostic: it is a backstop for whatever
        the primary splitter left oversized, not a place to add more structure
        awareness.
        """
        try:
            chunks = self.enforce_size(self.get_splitter(extension).split_documents(docs))
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
