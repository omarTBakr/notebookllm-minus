"""Getting a document off disk (or out of a byte string) and into Documents.

Loading only. What happens to the text afterwards — sanitising it, cutting it
into chunks — belongs to TextProcessingService, which this delegates to.
"""

import asyncio
import os
import tempfile
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from langchain_community.document_loaders import (  # ty: ignore[unresolved-import]
    PyPDFLoader,
    TextLoader,
)
from langchain_core.documents import Document  # ty: ignore[unresolved-import]

from shared.enums import FileExtension, PdfLoader, ProcessStatus
from shared.exceptions import ExtractionError, UnsupportedFileTypeError
from shared.utils import available_memory_mb, cpu_count

from ..core.BaseService import BaseService
from .PdfLayoutService import extract_pages, highlight_metadata
from .tabular import TabularLoader, continuation_prefix
from .TextProcessingService import TextProcessingService


def time_range_for(timeline: list, start: int, end: int) -> list[float]:
    """The ``[start_s, end_s]`` of video that text ``[start, end)`` came from.

    *timeline* is one ``[char_offset, start_s, end_s]`` per transcript line, in
    order. The chunk starts in the line holding its first character and ends
    with the line holding its last, so the range covers every line it quotes
    even when a chunk boundary falls mid-line.
    """
    offsets = [row[0] for row in timeline]

    first = max(0, bisect_right(offsets, start) - 1)
    last = max(first, bisect_right(offsets, max(start, end - 1)) - 1)

    return [timeline[first][1], timeline[last][2]]


class ProcessService(BaseService):
    def __init__(self, chunk_size=1000, chunk_overlap=200):
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Everything text-shaped goes through here.
        self.text = TextProcessingService(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        # Set only when process_file took the pymupdf word-layout path for a
        # PDF (PDF_LOADER=pymupdf) — keyed by page index, so split_file can
        # look a chunk's page back up to compute its highlight rectangle.
        # A fresh instance per upload (see routes/chat/assets.py), so this
        # never leaks between documents.
        self._pdf_pages: dict = {}
        # page index -> len(word-box text) / len(replacement text), for pages
        # whose text was replaced after extraction. Two things write it now --
        # the OCR re-read below, and the gemma4 correction pass that runs as its
        # own Celery stage -- so it is named for what it means rather than for
        # whichever one produced it. Empty when the text is the PDF's own.
        self._text_scale: dict = {}
        # page index -> [[char_offset, start_s, end_s], ...] for a page that is
        # a video transcript (see UrlSourceService.transcript_page). Lets
        # split_file give every chunk the stretch of video it came from.
        self._timelines: dict = {}

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
        elif extension in (FileExtension.CSV, FileExtension.XLSX):
            # One Document per row, each saying what its values are. See tabular.py
            # for what the stock CSV and Excel loaders did instead.
            return TabularLoader(file_path)
        elif extension in (FileExtension.TXT, FileExtension.MD):
            # A markdown file's structure is exactly what get_splitter's
            # language-aware separators want to see, so it is read as plain
            # text rather than through a loader that would convert it (and
            # strip the headings and fences that make the split worthwhile).
            return TextLoader(str(file_path))
        else:
            raise UnsupportedFileTypeError(f"Unsupported file type: {extension}")

    def _process_pdf_with_layout(self, file_path: Path, on_progress=None) -> list[Document]:
        """PDF extraction via PdfLayoutService, when PDF_LOADER=pymupdf.

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
        self._text_scale = {}

        text_by_page = self._reread_unusable_arabic(file_path, pages, on_progress)

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

    def process_file(self, file_path: Path, on_progress=None) -> list[Document]:
        extension = file_path.suffix.lower()

        if extension == FileExtension.PDF and self.settings.PDF_LOADER == PdfLoader.PYMUPDF:
            docs = self._process_pdf_with_layout(file_path, on_progress)
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

    def process_bytes(self, file_bytes: bytes, filename: str, on_progress=None) -> list[Document]:
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
            docs = self.process_file(tmp_path, on_progress)
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

    def _concurrency(self) -> int:
        """How many ingestion tasks this worker process runs at once.

        The resolved setting, not the raw field, and not a measurement of its
        own: `celery_worker_concurrency` is what the worker was actually
        started with, since celery_app sets `worker_concurrency` from it and
        the process worker passes no `--concurrency` flag to override it.
        Measuring here independently is what would let the two drift and
        over-subscribe the cores.
        """
        return max(1, getattr(self.settings, "celery_worker_concurrency", 1))

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
        available = available_memory_mb()
        affordable = max(1, int(available * 0.5 // self.OCR_PAGE_MB)) if available is not None else None

        if configured > 0:
            # Honoured, even above what the memory bound would pick. An
            # operator who sets this has a machine in front of them and may
            # know something this does not -- that swap exists, that the
            # monitoring stack is about to be moved off the box. Capping it
            # silently meant a 2-vCPU server could not be told to use both
            # cores at all: OCR ran single-threaded, a 214-page book took 428s
            # of a 540s budget, and ingestion died on the soft time limit with
            # the document left showing zero chunks.
            limit = configured

            if affordable is not None and configured > affordable:
                self.logger.warning(
                    "OCR_WORKERS=%d is above what free memory suggests (%d, from "
                    "%d MB available at ~%d MB a page). Honouring it, but a page "
                    "that does not fit is an OOM kill, not a slow page -- add swap "
                    "or lower it if the worker starts dying mid-document.",
                    configured,
                    affordable,
                    round(available),
                    self.OCR_PAGE_MB,
                )
        else:
            # Automatic: never more than the CPUs, never more than memory, and
            # never more than this process's *share* of the CPUs.
            #
            # That last bound is the one that was missing. A prefork worker
            # runs CELERY_WORKER_CONCURRENCY tasks at once, and each of them
            # sizes its own pool -- so on a 4-core box with concurrency 2, two
            # documents ingesting together asked for 4 threads each and put 8
            # tesseract processes on 4 cores. Measured while it was happening:
            # load average 8.66, six tesseract processes each getting ~48% of a
            # core instead of three getting ~100%. Aggregate CPU *looks*
            # moderate in a dashboard because the time goes into scheduling,
            # which is why the load average is the number to read.
            #
            # cpu_count() directly rather than Settings.available_cpus: they
            # are the same function, and reading it here keeps this pool
            # sizeable in a test without having to patch a pydantic property.
            share = max(1, cpu_count() // max(1, self._concurrency()))
            limit = share if affordable is None else min(share, affordable)

        return max(1, min(limit, pending))

    def ocr_unreadable_pages(self, file_path: Path, start: int, end: int) -> dict[int, str]:
        """OCR the pages of ``[start, end)`` that have no usable text layer.

        Returns OCR text keyed by page index; pages absent from it keep what
        the PDF gave. Off unless OCR_UNREADABLE_PAGES is set.

        Not the same job as `_reread_unusable_arabic`. That one repairs pages
        whose text is *there* but badly spaced, and only on the single-process
        path. This one is for pages that come out empty or as glyph garbage --
        a scanned page, a title page drawn as a picture -- which nothing else
        in the pipeline reads: `profile()` skips any page under OCR_MIN_CHARS,
        so a page with no text at all was silently indexed as nothing.
        On ذخائر_لبنان.pdf that was 6 of 222 pages.

        qalam decides which pages, since it is the one extractor that says so
        per page; tesseract-best reads them with Arabic and English together,
        because a scanned page is as likely to be the English one. Either being
        unavailable is a warning, not a failure: the ingest continues with the
        text layer as it would have before this existed.
        """
        if not getattr(self.settings, "OCR_UNREADABLE_PAGES", False):
            return {}

        from application.arabic_extraction.base import Page as OcrPage
        from application.arabic_extraction.extractors.qalam_extractor import (
            QalamExtractor,
        )
        from application.arabic_extraction.registry import build

        ok, reason = QalamExtractor.available()

        if not ok:
            self.logger.warning("Cannot find unreadable pages of %s: %s", file_path.name, reason)
            return {}

        try:
            flagged = QalamExtractor.pages_needing_ocr(file_path, start, end)
        except Exception as exc:  # noqa: BLE001 - qalam failing must not fail the ingest
            self.logger.warning("qalam could not check pages %d-%d of %s: %s", start, end - 1, file_path.name, exc)
            return {}

        if not flagged:
            return {}

        extractors = build(["tesseract-best"], **{"tesseract-best": {"lang": "ara+eng"}})

        if not extractors:
            self.logger.warning(
                "%d page(s) of %s have no usable text layer, but tesseract-best cannot run; " "they stay empty",
                len(flagged),
                file_path.name,
            )
            return {}

        extractor = extractors[0]
        extractor.warm_up()

        # See _reread_unusable_arabic: tesseract's own threads under this pool
        # oversubscribe every core.
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")

        def read(index):
            return index, extractor.run(OcrPage(path=file_path, number=index))

        replacements: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=self._ocr_workers(len(flagged))) as pool:
            for index, result in pool.map(read, flagged):
                if not result.ok or not result.text.strip():
                    self.logger.warning(
                        "OCR found nothing on page %d of %s: %s",
                        index + 1,
                        file_path.name,
                        result.error or "empty output",
                    )
                elif not self._reads_as_text(result.text):
                    self.logger.info(
                        "OCR of page %d of %s is not text (a picture, most likely); keeping it empty",
                        index + 1,
                        file_path.name,
                    )
                else:
                    replacements[index] = result.text

        self.logger.info(
            "OCR'd %d of %d unreadable page(s) in pages %d-%d of %s",
            len(replacements),
            len(flagged),
            start,
            end - 1,
            file_path.name,
        )

        return replacements

    @staticmethod
    def _word_ratio(text: str) -> tuple[int, float]:
        """Real words in *text*, and their share of the tokens that have letters.

        A word is three or more letters (or the Arabic marks that sit on them,
        with a hyphen or apostrophe allowed inside) once edge punctuation is
        stripped. Tokens with no letter at all -- page numbers, dot leaders,
        `©.` -- count toward neither side: a table of contents is half of them
        and is still a real page.
        """
        import unicodedata

        def letter(c: str) -> bool:
            return c.isalpha() or unicodedata.category(c).startswith("M")

        lettered = [token for token in text.split() if any(letter(c) for c in token)]

        if not lettered:
            return 0, 0.0

        words = 0

        for token in lettered:
            core = token.strip(".,،؛;:!?()[]{}«»\"'-—…")
            if sum(letter(c) for c in core) >= 3 and all(letter(c) or c in "-'’" for c in core):
                words += 1

        return words, words / len(lettered)

    @classmethod
    def _reads_as_text(cls, text: str) -> bool:
        """Whether OCR output is text rather than tesseract reading a picture.

        The pages qalam flags are often not text at all -- a cover photograph,
        a map, a decorative title -- and tesseract still returns something for
        them: on ذخائر_لبنان.pdf's cover, `©. L ©. REE pn. SIAC PUI EQN Zo`.
        Indexed, that is noise a retrieval can match; empty, it is nothing.

        So: at least half the lettered tokens are real words, and there are at
        least two. The ratio does the separating -- that cover scores well
        under half, scanned prose and a scanned table of contents well over.
        The floor is low on purpose: a slide whose only text is "Why
        statistics?" is a real page, and dropping it is the loss this pass
        exists to prevent.
        """
        words, ratio = cls._word_ratio(text)

        return words >= 2 and ratio >= 0.5

    def _reread_unusable_arabic(self, file_path: Path, pages, on_progress=None) -> dict[int, str]:
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

        from application.arabic_extraction.base import Page as OcrPage
        from application.arabic_extraction.language import profile
        from application.arabic_extraction.registry import build

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
            from application.arabic_extraction.registry import survey

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
        done = 0
        results = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            # as_completed rather than map: map yields in submission order, so
            # progress would advance in lockstep with the slowest page ahead of
            # it rather than as pages actually finish. On a 214-page book that
            # is the difference between a bar that moves and one that sits at
            # zero for minutes -- which is what a user reads as "hung".
            futures = [pool.submit(read, page) for page in candidates]

            for future in as_completed(futures):
                results.append(future.result())
                done += 1

                if on_progress is not None:
                    on_progress(done, len(candidates))

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
                self._text_scale[page.page_index] = len(page.text) / len(result.text)

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
        """Chunk the extracted text. Delegates; see TextProcessingService.

        ``extension`` selects a structure-aware separator list for a format
        that has one — currently only ``.md``. Everything else keeps the
        prose splitter unchanged.

        When ``_process_pdf_with_layout`` ran, each resulting chunk also gets
        a ``highlight`` key in its metadata — the rectangles a citation draws
        over the cited passage. Silently absent otherwise: a chunk from any
        other loader, or one whose ``start_index`` the size guard could not
        rebase (see TextProcessingService.enforce_size), simply has no
        highlight, which the citation UI already treats as "nothing to draw."
        """
        chunks = self.text.split(docs, extension=extension)

        self._repeat_row_identity(chunks)

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
                    scale=self._text_scale.get(chunk.metadata.get("page"), 1.0),
                )
                if highlight is not None:
                    chunk.metadata["highlight"] = highlight

        if self._timelines:
            for chunk in chunks:
                timeline = self._timelines.get(chunk.metadata.get("page"))
                start = chunk.metadata.get("start_index")

                if not timeline or not isinstance(start, int) or start < 0:
                    continue

                chunk.metadata["time_range"] = time_range_for(timeline, start, start + len(chunk.page_content))

        return chunks

    def _repeat_row_identity(self, chunks: list[Document]) -> None:
        """Head each continuation chunk of a long table row with whose row it is.

        A row is one document, so it is normally one chunk. When a cell is long
        enough to be split, only the first piece begins with the row's cells; the
        rest are plain text that says nothing about which record it belongs to, and
        a search that lands on one cannot tell. `start_index > 0` is what marks a
        piece as a continuation.
        """
        limit = max(self.chunk_size // 4, 1)

        for chunk in chunks:
            metadata = chunk.metadata

            if not metadata.get("table") or not metadata.get("start_index"):
                continue

            chunk.page_content = continuation_prefix(metadata, limit) + chunk.page_content

    async def process_and_split(self, file_bytes: bytes, filename: str, on_progress=None) -> list[Document]:
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
                self.process_bytes(file_bytes, filename, on_progress),
                extension=extension,
            )

        return await asyncio.to_thread(work)
