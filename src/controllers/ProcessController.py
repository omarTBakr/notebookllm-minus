import tempfile
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from enums import ProcessStatus
from exceptions import ChunkingError, ExtractionError, UnsupportedFileTypeError

from .BaseController import BaseController
from routes.schemas import FileExtension


class ProcessController(BaseController):
    def __init__(self, chunk_size=1000, chunk_overlap=200):
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def get_loader(self, file_path: Path):
        extension = file_path.suffix.lower()
        if extension == FileExtension.PDF:
            return PyPDFLoader(str(file_path))
        elif extension == FileExtension.TXT:
            return TextLoader(str(file_path))
        else:
            raise UnsupportedFileTypeError(f"Unsupported file type: {extension}")

    def get_splitter(self) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )

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
            return self.process_file(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def split_file(self, docs: list[Document]) -> list[Document]:
        try:
            splitter = self.get_splitter()
            chunks = splitter.split_documents(docs)
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
