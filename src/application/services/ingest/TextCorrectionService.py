"""Repairing a page of extracted text with a local model.

PyMuPDF loses no glyphs, but a PDF's text layer is a list of drawing
instructions rather than a document: words arrive split where the font changed,
fused where no space was ever emitted, hyphenated across a line break that no
longer exists. The text is *nearly* right, and nearly right is what makes it
unsearchable -- a query for a word the layer stored in two halves matches
nothing at all.

This is the pass that fixes that, and it replaces the tesseract re-read rather
than joining it: OCR throws away the per-word boxes a citation highlight is
drawn from and costs seconds a page to rasterise, where this keeps the boxes
and costs a model call.

The whole design problem is that the repair is a rewrite, and a rewrite is
indistinguishable from a fabrication once it has been stored. A small model
handed a page will translate it, summarise it, or answer a question it happens
to contain -- all of which come back as fluent, well-formed, schema-valid text
that no validator can reject. So the prompt forbids them (see
`prompts/en/ocr_correction.py`) and `_accept` below checks the one property a
faithful reproduction must have and a summary cannot: it is about as long as
what went in.
"""

import asyncio

from pydantic import BaseModel, Field

from application.ocr_prompts import get_prompt
from shared.exceptions import StructuredOutputError
from shared.utils import get_logger

from ..core.BaseService import BaseService
from ..llm.structured_generation import generate_structured

logger = get_logger(__name__)


class CorrectedPage(BaseModel):
    """One page as the model returned it, under the number it was shown.

    `num` is the *batch-local* number from the prompt -- 1, 2, 3 within this
    call -- and deliberately not the page index. The same lesson as
    ChunkSummary.num, learned again here the same way: shown a 0-based
    page_index, gemma4 answered with 12 for the page whose text began "Page 12"
    but whose index was 11. It had been given two plausible numbers for one
    page and picked the one printed on the paper. A number that only exists
    inside the prompt has no competing interpretation.
    """

    num: int = Field(..., ge=1)
    text: str = Field(..., min_length=1)


class CorrectionSet(BaseModel):
    """What one correction call is expected to return.

    `pages` is REQUIRED -- `Field(...)`, not `default_factory=list` -- for the
    reason recorded at length in data/models/artifact/summary.py: with a
    default, a model that echoes the schema back instead of filling it in
    validates cleanly as zero pages, `generate_structured` calls that a success,
    its repair loop never fires, and the batch is dropped in silence. Here that
    would mean pages silently keeping their damaged text, which looks exactly
    like the feature being switched off.
    """

    pages: list[CorrectedPage] = Field(...)

    # Shown to the model as the shape to imitate. See schema_instruction.
    model_config = {
        "json_schema_extra": {
            "example": {
                "pages": [
                    {"num": 1, "text": "The first page, reproduced in full."},
                    {"num": 2, "text": "The second page, reproduced in full."},
                ]
            }
        }
    }


class TextCorrectionService(BaseService):
    """Corrects extracted page text, one model call at a time.

    Holds no state between calls and opens nothing: the client is handed in, so
    the Celery task owns its lifetime and a test can pass a fake.
    """

    def __init__(self, client, lang: str | None = None) -> None:

        super().__init__()

        self.client = client
        self.lang = lang

        self.pages_per_call = max(1, self.settings.POSTPROCESS_PAGES_PER_CALL)
        self.tolerance = self.settings.POSTPROCESS_LENGTH_TOLERANCE
        self.max_tokens = self.settings.POSTPROCESS_MAX_TOKENS

    # --- the guard rail --------------------------------------------------------

    def _accept(self, original: str, corrected: str, page: int) -> bool:
        """Whether *corrected* is plausibly *original* repaired.

        A length band, because length is the one cheap invariant a faithful
        reproduction has. Every way this pass fails badly -- summarising,
        answering, translating to a denser script, stopping halfway because the
        context window was too small -- changes the length by much more than
        joining hyphens and inserting spaces ever does. Every way it succeeds
        leaves it within a few percent.

        It is not a correctness check and does not pretend to be: a model that
        rewrites a page into different words of the same length passes. It is
        a floor that catches the failures actually observed, cheaply, without a
        second model call to judge the first.
        """
        if not corrected.strip():
            self.logger.warning("Correction for page %d came back empty; keeping the extracted text", page)
            return False

        if not original:
            return True

        ratio = len(corrected) / len(original)

        if not (1 - self.tolerance) <= ratio <= (1 + self.tolerance):
            self.logger.warning(
                "Correction for page %d is %.0f%% of the original length "
                "(%d -> %d chars), outside the ±%.0f%% band; keeping the "
                "extracted text. A short answer here is usually the model "
                "summarising the page instead of reproducing it.",
                page,
                ratio * 100,
                len(original),
                len(corrected),
                self.tolerance * 100,
            )
            return False

        return True

    # --- one call --------------------------------------------------------------

    def _prompt(self, group: list[dict]) -> str:
        """The full correction prompt for one call's worth of pages.

        Pages are numbered from 1 *within this call*. See CorrectedPage.num.
        """
        blocks = [
            get_prompt(
                "ocr_correction",
                "page_prompt",
                {"num": num, "text": page["text"]},
                lang=self.lang,
            )
            for num, page in enumerate(group, start=1)
        ]

        return get_prompt(
            "ocr_correction",
            "correction_prompt",
            {"pages": "\n\n".join(blocks)},
            lang=self.lang,
        )

    async def _correct_group(self, group: list[dict]) -> dict[int, str]:
        """Correct one call's worth of pages, or none of them.

        A failure is logged and swallowed. The caller keeps the extracted text,
        which is the whole point of running this pass on a text layer that was
        already readable: a local model being down, slow, or bad at JSON is not
        a reason to fail an upload that has perfectly usable text in it.
        """
        try:
            result = await generate_structured(
                self.client,
                self._prompt(group),
                CorrectionSet,
                max_tokens=self.max_tokens,
            )

        except StructuredOutputError as exc:
            self.logger.warning(
                "Correction failed for page(s) %s: %s; keeping the extracted text",
                [page["page_index"] for page in group],
                exc,
            )
            return {}

        except Exception as exc:
            # Broad on purpose: a provider error, a dead socket, a timeout.
            # Every one of them means the same thing here.
            self.logger.warning(
                "Correction call failed for page(s) %s: %s; keeping the extracted text",
                [page["page_index"] for page in group],
                exc,
            )
            return {}

        # Mapped back through the batch-local number, not by position: a model
        # that reorders or drops a page must not have its answer silently
        # assigned to a different one. A number outside the range it was shown
        # belongs to no page at all and is dropped by the caller.
        numbering = {num: page["page_index"] for num, page in enumerate(group, start=1)}

        return {numbering[page.num]: page.text for page in result.pages if page.num in numbering}

    # --- the batch -------------------------------------------------------------

    async def correct(self, pages: list[dict]) -> dict[int, dict]:
        """Correct a batch of pages.

        *pages* are dicts with at least ``page_index`` and ``text``. Returns
        ``{page_index: {"text": str, "scale": float}}`` containing only the
        pages that were actually replaced -- a page absent from the result keeps
        what the PDF gave it.

        ``scale`` is ``len(original) / len(corrected)``, which is what
        `highlight_metadata` needs to map a chunk's character offsets in the
        corrected string back onto word boxes measured against the original.
        Without it every corrected page silently loses its citation highlight.
        """
        if not pages:
            return {}

        # A page with no text layer has nothing to repair, and sending it is
        # not merely wasteful -- it poisons the call it travels in. The model
        # correctly answers a blank page with a blank string, `CorrectedPage`
        # requires a non-empty one, and the whole group then fails validation
        # three times over and is dropped. Measured on a 222-page book with six
        # blank pages: every batch carrying one lost all ten of its pages.
        #
        # Filtered here rather than loosened in the schema, because an empty
        # `text` for a page that *did* have text is a real failure and must
        # keep failing.
        pages = [page for page in pages if page.get("text", "").strip()]

        if not pages:
            return {}

        groups = [pages[i : i + self.pages_per_call] for i in range(0, len(pages), self.pages_per_call)]

        # Bounded rather than all at once: Ollama serialises requests per model
        # anyway, so an unbounded gather would only queue them inside the
        # server while holding every page's text in memory here.
        limit = asyncio.Semaphore(max(1, self.settings.POSTPROCESS_CONCURRENCY))

        async def run(group):
            async with limit:
                return await self._correct_group(group)

        replacements: dict[int, dict] = {}
        by_index = {page["page_index"]: page["text"] for page in pages}

        for corrected in await asyncio.gather(*(run(group) for group in groups)):
            for index, text in corrected.items():
                original = by_index.get(index)

                if original is None:
                    # The model invented a page number. Dropping it is the only
                    # safe reading: there is nothing to compare it against and
                    # nothing it could be attached to.
                    self.logger.warning("Correction returned unknown page %r; ignoring it", index)
                    continue

                if not self._accept(original, text, index):
                    continue

                replacements[index] = {
                    "text": text,
                    "scale": len(original) / len(text),
                }

        if replacements:
            self.logger.info(
                "Corrected %d of %d page(s) with %s",
                len(replacements),
                len(pages),
                self.settings.POSTPROCESS_MODEL_ID,
            )

        return replacements
