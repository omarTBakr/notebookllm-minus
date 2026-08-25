"""Getting a document off disk (or out of a byte string) and into Documents.

Loading only. What happens to the text afterwards — sanitising it, cutting it
into chunks — belongs to TextProcessingController, which this delegates to.
"""

import asyncio
import tempfile
from pathlib import Path

from langchain_core.documents import Document  # ty: ignore[unresolved-import]
from langchain_community.document_loaders import TextLoader, PyPDFLoader # ty: ignore[unresolved-import]

from enums import FileExtension, ProcessStatus
from exceptions import ExtractionError, UnsupportedFileTypeError
from .BaseController import BaseController
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

    def get_loader(self, file_path: Path):
        extension = file_path.suffix.lower()
        if extension == FileExtension.PDF:
            return PyPDFLoader(str(file_path))
        elif extension == FileExtension.TXT:
            return TextLoader(str(file_path))
        else:
            raise UnsupportedFileTypeError(f"Unsupported file type: {extension}")

    def process_file(self, file_path: Path) -> list[Document]:
        # get_loader raises UnsupportedFileTypeError (a 400) — let it through
        # rather than reporting an unreadable format as a server fault.
        loader = self.get_loader(file_path)

        try:
            docs = loader.load()
        except Exception as exc:
            raise ExtractionError(
                f"{ProcessStatus.EXTRACTION_FAILED.value}: {file_path.name}"
            ) from exc

        # Before anything else sees it: the loaders can emit text the stores
        # will not accept.
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

    def split_file(self, docs: list[Document]) -> list[Document]:
        """Chunk the extracted text. Delegates; see TextProcessingController."""
        return self.text.split(docs)

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

        def work() -> list[Document]:
            return self.split_file(self.process_bytes(file_bytes, filename))

        return await asyncio.to_thread(work)
