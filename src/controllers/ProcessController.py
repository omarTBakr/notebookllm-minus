"""Getting a document off disk (or out of a byte string) and into Documents.

Loading only. What happens to the text afterwards — sanitising it, cutting it
into chunks — belongs to TextProcessingController, which this delegates to.
"""

import asyncio
import tempfile
from pathlib import Path

from langchain_core.documents import Document  # ty: ignore[unresolved-import]
from langchain_community.document_loaders import TextLoader, PyPDFLoader # ty: ignore[unresolved-import]

from enums import FileExtension, PdfLoader, ProcessStatus
from exceptions import ExtractionError, UnsupportedFileTypeError
from .BaseController import BaseController
from .PdfLayoutController import extract_pages, highlight_metadata
from .TextProcessingController import TextProcessingController


class ProcessController(BaseController):
    def __init__(self, chunk_size=1000, chunk_overlap=200):
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Everything text-shaped goes through here.
        self.text = TextProcessingController(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        # Set only when process_file took the pymupdf word-layout path for a
        # PDF (PDF_LOADER=pymupdf) — keyed by page index, so split_file can
        # look a chunk's page back up to compute its highlight rectangle.
        # A fresh instance per upload (see routes/chat/assets.py), so this
        # never leaks between documents.
        self._pdf_pages: dict = {}

    def _pdf_loader(self, file_path: Path):
        """The PDF extractor named by PDF_LOADER.

        Imported here rather than at module scope so that installing only the
        library you actually use is enough — pdfplumber and pymupdf are both
        heavy, and neither is needed to run the default.

        The choice matters more than it looks. Measured on the 274-page Arabic
        guide, whole document, counting real words recovered after NFKC:

            pypdf        29s   8-9/14 words   90,874 lost glyphs
            pdfplumber   52s     0/14 words   92,514 lost glyphs
            pymupdf     543s    11/14 words        0 lost glyphs

        pdfplumber's zero is not a bug in the probe: it lays characters out by
        x-position, which for right-to-left script emits every line *reversed*.
        The text looks plausible and matches nothing.
        """
        loader = self.settings.PDF_LOADER

        if loader == PdfLoader.PYPDF:
            return PyPDFLoader(str(file_path))

        # pymupdf alone is ~64 MB installed, so these may be trimmed out of a
        # slim image. Say which package is missing rather than letting a bare
        # ImportError surface as a 500 on upload.
        try:
            if loader == PdfLoader.PDFPLUMBER:
                from langchain_community.document_loaders import PDFPlumberLoader

                return PDFPlumberLoader(str(file_path))

            from langchain_community.document_loaders import PyMuPDFLoader

            return PyMuPDFLoader(str(file_path))
        except ImportError as exc:
            raise ExtractionError(
                f"PDF_LOADER is {loader!r} but its library is not installed "
                f"({exc}). Install it, or set PDF_LOADER=pypdf."
            ) from exc

    def get_loader(self, file_path: Path):
        extension = file_path.suffix.lower()
        if extension == FileExtension.PDF:
            return self._pdf_loader(file_path)
        elif extension in (FileExtension.TXT, FileExtension.MD):
            # A markdown file's structure is exactly what get_splitter's
            # language-aware separators want to see, so it is read as plain
            # text rather than through a loader that would convert it (and
            # strip the headings and fences that make the split worthwhile).
            return TextLoader(str(file_path))
        else:
            raise UnsupportedFileTypeError(f"Unsupported file type: {extension}")

    def _process_pdf_with_layout(self, file_path: Path) -> list[Document]:
        """PDF extraction via PdfLayoutController, when PDF_LOADER=pymupdf.

        Bypasses get_loader/langchain's PyMuPDFLoader entirely: that loader
        only ever exposes page *text*, never the per-word bounding boxes a
        highlight is computed from. Building the Documents straight from
        PageWords instead means the text the splitter cuts and the boxes a
        chunk's rects come from are guaranteed to be the same text — anything
        routed back through a second, independent extraction pass could not
        promise that.

        Pages are kept on the instance, keyed by index, so split_file can
        look one back up after splitting; the returned Documents themselves
        are shaped exactly like any other loader's output.
        """
        try:
            pages = extract_pages(file_path)
        except Exception as exc:
            raise ExtractionError(
                f"{ProcessStatus.EXTRACTION_FAILED.value}: {file_path.name}"
            ) from exc

        self._pdf_pages = {page.page_index: page for page in pages}

        return [
            Document(
                page_content=page.text,
                metadata={
                    "source": file_path.name,
                    "page": page.page_index,
                    "page_label": page.page_label,
                    "total_pages": len(pages),
                },
            )
            for page in pages
        ]

    def process_file(self, file_path: Path) -> list[Document]:
        extension = file_path.suffix.lower()

        if extension == FileExtension.PDF and self.settings.PDF_LOADER == PdfLoader.PYMUPDF:
            docs = self._process_pdf_with_layout(file_path)
        else:
            # get_loader raises UnsupportedFileTypeError (a 400) — let it
            # through rather than reporting an unreadable format as a server
            # fault.
            loader = self.get_loader(file_path)

            try:
                docs = loader.load()
            except Exception as exc:
                raise ExtractionError(
                    f"{ProcessStatus.EXTRACTION_FAILED.value}: {file_path.name}"
                ) from exc

        # Before anything else sees it: the loaders can emit text the stores
        # will not accept. A no-op on the layout path above — extract_pages
        # already normalises per word — but it still runs, so that guarantee
        # is enforced in one place rather than trusted from two.
        self.text.sanitize(docs, source=file_path.name)

        self.logger.info(
            "Extracted %d document(s) from %s", len(docs), file_path.name
        )
        return docs

    def process_bytes(self, file_bytes: bytes, filename: str) -> list[Document]:
        """Load a document from raw bytes without leaving a permanent file on disk.

        Writes *file_bytes* to a named temp file (preserving the original
        extension so the loader picks the right parser), processes it, then
        deletes the temp file — even if an error occurs.
        """
        suffix = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        try:
            docs = self.process_file(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        # The loaders stamp metadata["source"] with the temp file's path, which
        # is meaningless the moment that file is deleted — and it leaks a server
        # path into the stored chunk and the API response. Point it at the real
        # document name, which is what a citation will need anyway.
        for doc in docs:
            doc.metadata["source"] = filename

        return docs

    def split_file(self, docs: list[Document], extension: str | None = None) -> list[Document]:
        """Chunk the extracted text. Delegates; see TextProcessingController.

        ``extension`` selects a structure-aware separator list for a format
        that has one — currently only ``.md``. Everything else keeps the
        prose splitter unchanged.

        When ``_process_pdf_with_layout`` ran, each resulting chunk also gets
        a ``highlight`` key in its metadata — the rectangles a citation draws
        over the cited passage. Silently absent otherwise: a chunk from any
        other loader, or one whose ``start_index`` the size guard could not
        rebase (see TextProcessingController.enforce_size), simply has no
        highlight, which the citation UI already treats as "nothing to draw."
        """
        chunks = self.text.split(docs, extension=extension)

        if self._pdf_pages:
            for chunk in chunks:
                page = self._pdf_pages.get(chunk.metadata.get("page"))
                start = chunk.metadata.get("start_index")

                if page is None or start is None or start < 0:
                    continue

                highlight = highlight_metadata(page, start, start + len(chunk.page_content))
                if highlight is not None:
                    chunk.metadata["highlight"] = highlight

        return chunks

    async def process_and_split(self, file_bytes: bytes, filename: str) -> list[Document]:
        """Extract and split, off the event loop.

        Both halves are synchronous CPU work — pypdf parsing every page, then
        the splitter walking the whole text — and neither yields. Called
        straight from an `async def` route they run *on* the event loop, which
        is what stopped the server answering anything at all while a large PDF
        was ingesting: one 200-page upload froze every other request behind it.

        One thread hop covers both steps rather than one each, so the loop is
        released once and the intermediate document list never crosses back.
        """
        extension = Path(filename).suffix.lower()

        def work() -> list[Document]:
            return self.split_file(
                self.process_bytes(file_bytes, filename), extension=extension
            )

        return await asyncio.to_thread(work)
