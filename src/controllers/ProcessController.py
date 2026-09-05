"""Getting a document off disk (or out of a byte string) and into Documents.

Loading only. What happens to the text afterwards — sanitising it, cutting it
into chunks — belongs to TextProcessingController, which this delegates to.
"""

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_community.document_loaders import (  # ty: ignore[unresolved-import]
    PyPDFLoader,
    TextLoader,
)
from langchain_core.documents import Document  # ty: ignore[unresolved-import]

from enums import FileExtension, PdfLoader, ProcessStatus
from exceptions import ExtractionError, UnsupportedFileTypeError

from .BaseController import BaseController
from .PdfLayoutController import (
    _available_memory_mb,
    _cpu_count,
    extract_pages,
    highlight_metadata,
)
from .TextProcessingController import TextProcessingController


class ProcessController(BaseController):
    def __init__(self, chunk_size=1000, chunk_overlap=200):
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Everything text-shaped goes through here.
        self.text = TextProcessingController(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        # Set only when process_file took the pymupdf word-layout path for a
        # PDF (PDF_LOADER=pymupdf) — keyed by page index, so split_file can
        # look a chunk's page back up to compute its highlight rectangle.
        # A fresh instance per upload (see routes/chat/assets.py), so this
        # never leaks between documents.
        self._pdf_pages: dict = {}
        # page index -> len(word-box text) / len(OCR text), for pages that were
        # re-read. Empty unless OCR ran.
        self._ocr_scale: dict = {}

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
            raise ExtractionError(f"{ProcessStatus.EXTRACTION_FAILED.value}: {file_path.name}") from exc

        self._pdf_pages = {page.page_index: page for page in pages}
        self._ocr_scale = {}

        text_by_page = self._reread_unusable_arabic(file_path, pages)

        return [
            Document(
                page_content=text_by_page.get(page.page_index, page.text),
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
                raise ExtractionError(f"{ProcessStatus.EXTRACTION_FAILED.value}: {file_path.name}") from exc

        # Before anything else sees it: the loaders can emit text the stores
        # will not accept. A no-op on the layout path above — extract_pages
        # already normalises per word — but it still runs, so that guarantee
        # is enforced in one place rather than trusted from two.
        self.text.sanitize(docs, source=file_path.name)

        self.logger.info("Extracted %d document(s) from %s", len(docs), file_path.name)
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

    #: Peak memory one concurrent page costs, in MB. Measured in the
    #: application image: 117 MB for the tesseract-best process itself, a 25 MB
    #: 300-dpi RGB raster, and the PNG copy plus the temp file pytesseract
    #: hands the binary. Rounded up, because a dense book page costs more than
    #: the page this was measured on.
    OCR_PAGE_MB = 256

    def _ocr_workers(self, pending: int) -> int:
        """How many pages to OCR at once.

        Bounded by three things, the smallest winning:

        *The pages there are.* A two-page document does not start twenty-four
        threads to do two pages' work.

        *The CPUs this process may use.* cgroup quota and affinity included.

        *The memory it may use.* This is the one that bit. OCR_WORKERS
        defaulted to the CPU count alone, which on a 24-core host with 5 GB
        free started 24 tesseract processes at ~200 MB each and the kernel
        OOM-killed the worker mid-document — `WorkerLostError: signal 9`,
        after which celery retried and the upload merely looked slow. Half the
        available memory is spent here at most, so the rest of the process
        keeps room to hold the extracted document it is OCR'ing *for*.

        An explicit OCR_WORKERS is still capped by memory: the setting says how
        much parallelism is wanted, not how much the box can survive.
        """
        configured = getattr(self.settings, "OCR_WORKERS", 0)
        limit = configured if configured > 0 else _cpu_count()

        available = _available_memory_mb()
        if available is not None:
            limit = min(limit, int(available * 0.5 // self.OCR_PAGE_MB))

        return max(1, min(limit, pending))

    def _reread_unusable_arabic(self, file_path: Path, pages) -> dict[int, str]:
        """OCR the pages whose Arabic text layer cannot be searched.

        Returns replacement text keyed by page index; pages absent from it keep
        what the PDF gave. Nothing happens at all unless OCR_ENABLED is set.

        Two things make this narrower than "OCR the document":

        *Only Arabic, and only when broken.* `profile()` costs microseconds and
        answers both questions. A healthy text layer is the characters the
        author typed — re-reading it with OCR trades those for a guess at the
        pixels, which is strictly worse as well as seconds slower.

        *An OCR'd page keeps an approximate highlight.* The rectangles a
        citation draws come from `highlight_metadata`, which maps character
        offsets onto the word boxes this page's text was built from. Replacing
        the text means those offsets index a different string — but the boxes
        remain the only positional information in existence, since OCR returns
        none, and both strings read the same page in the same order. So the
        page is kept and the length ratio recorded here; `split_file` scales
        offsets through it, and the highlight is marked `approx`. Chunks are a
        fixed size and cover a good fraction of a page, so landing in the right
        region is what this needs to do.
        """
        if not self.settings.OCR_ENABLED:
            return {}

        from ocr.base import Page as OcrPage
        from ocr.language import profile
        from ocr.registry import build

        candidates = [
            page
            for page in pages
            if (details := profile(page.text)).is_arabic
            and not details.is_usable
            and details.characters >= self.settings.OCR_MIN_CHARS
        ]

        if not candidates:
            return {}

        extractors = build([self.settings.OCR_EXTRACTOR])

        if not extractors:
            from ocr.registry import survey

            reason = {entry.name: entry.reason for entry in survey()}.get(
                self.settings.OCR_EXTRACTOR, "unknown extractor"
            )
            # A warning, not an error: the text layer is poor, not absent, and
            # failing the whole upload over a missing OCR engine would be a
            # worse outcome than indexing what the PDF already gave us.
            self.logger.warning(
                "OCR_ENABLED but %r cannot run (%s); keeping the text layer for " "%d unusable page(s) of %s",
                self.settings.OCR_EXTRACTOR,
                reason,
                len(candidates),
                file_path.name,
            )
            return {}

        extractor = extractors[0]
        extractor.warm_up()

        # Tesseract is built with OpenMP and will otherwise start its own
        # threads per page. Stacked under the pool below that oversubscribes
        # every core several times over and runs slower than either alone.
        # One page per thread, one thread per page: page level parallelism
        # scales far better than tesseract's internal threading, and this is
        # the documented way to turn the latter off.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")

        workers = self._ocr_workers(len(candidates))

        replacements: dict[int, str] = {}

        def read(page):
            return page, extractor.run(OcrPage(path=file_path, number=page.page_index))

        # Threads are safe here: `run` keeps no state on the extractor, and
        # each Page opens its own pymupdf handle. The per-call telemetry it
        # returns is not — `process_time` and RSS are process-wide — but none
        # of it is read on this path. The benchmark, which does read it, stays
        # serial for exactly that reason.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(read, candidates))

        for page, result in results:
            if not result.ok:
                self.logger.warning(
                    "OCR failed on page %d of %s: %s",
                    page.page_index + 1,
                    file_path.name,
                    result.error or "empty output",
                )
                continue

            replacements[page.page_index] = result.text

            # Keep the page and record how the two strings differ in length.
            # The boxes still describe where things are on the paper — OCR
            # produces no coordinates at all — and both strings read the page
            # in the same order, so split_file can map an offset from one into
            # the other. See highlight_metadata: the result is marked approximate.
            if result.text:
                self._ocr_scale[page.page_index] = len(page.text) / len(result.text)

        if replacements:
            self.logger.info(
                "Re-read %d of %d page(s) of %s with %s (unusable Arabic text layer)",
                len(replacements),
                len(pages),
                file_path.name,
                extractor.name,
            )

        return replacements

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

                highlight = highlight_metadata(
                    page,
                    start,
                    start + len(chunk.page_content),
                    scale=self._ocr_scale.get(chunk.metadata.get("page"), 1.0),
                )
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
            return self.split_file(self.process_bytes(file_bytes, filename), extension=extension)

        return await asyncio.to_thread(work)
