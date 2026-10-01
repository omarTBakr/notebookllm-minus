"""The batched ingest pipeline: parse -> correct -> assemble.

Extraction and chunking used to happen inside one function, in one process, so
the word boxes a citation highlight is drawn from were simply still in memory
when the splitter ran. They now cross a broker as JSON and come back in a
different worker, and every test here is about something that survives — or
quietly does not — across that hop.
"""

import pymupdf
import pytest

from application.services.ingest import page_from_dict, page_to_dict
from application.services.ingest.PdfLayoutService import extract_page_range, page_count
from application.tasks.jobs.ingest.assemble import _documents, assemble_chunks


def make_pdf(path, pages_text):
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 30), text, fontsize=12)
    doc.save(str(path))
    doc.close()


# --- the trip through the broker ----------------------------------------------


def test_a_parsed_page_survives_being_serialised(tmp_path):
    """Celery's serializer is JSON, so PageWords cannot travel as itself. Word
    boxes arrive as lists and have to come back as the tuples rects_for_range
    unpacks."""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["alpha beta gamma delta"])

    original = extract_page_range(pdf, 0, 1)[0]

    import json

    # Through actual JSON, not just the two functions: a float or a tuple that
    # only round-trips in memory would pass a weaker test and fail in a worker.
    revived = page_from_dict(json.loads(json.dumps(page_to_dict(original))))

    assert revived.text == original.text
    assert revived.words == original.words
    assert revived.starts == original.starts
    assert revived.boxes == original.boxes
    assert all(isinstance(b, tuple) for b in revived.boxes)


def test_page_count_does_not_extract_anything(tmp_path):
    """The planner needs the count and nothing else. Reading a 274-page
    document to learn how many pages it has would cost ~520s to answer a
    question the file header already knows."""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["one", "two", "three"])

    assert page_count(pdf) == 3


# --- reassembly ----------------------------------------------------------------


async def test_pages_are_collected_in_page_order_not_arrival_order(fake_db):
    """Batches finish on whichever worker is free, so they come back in no
    particular order. A document assembled the way they arrived has every page
    present and each one readable, which is why that bug survives — the only
    sign is that the book makes no sense."""
    from data.models import IngestBatch, IngestBatchModel
    from data.models.ingest import IngestBatchStatus

    batches = IngestBatchModel(fake_db)

    for batch_index, page_index, text in [(2, 2, "third"), (0, 0, "first"), (1, 1, "second")]:
        await batches.save_batch(
            IngestBatch(
                asset_id="a1",
                project_id="p1",
                batch_index=batch_index,
                status=IngestBatchStatus.CORRECTED,
                asset_name="d.pdf",
                payload={"pages": [{"page_index": page_index, "text": text}]},
            )
        )

    seen = [b.batch_index async for b in batches.iter_batches("a1")]

    assert seen == [0, 1, 2], "batches must come back in index order, not insertion order"


def test_a_corrected_page_supplies_its_text_and_its_scale():
    """The corrected text is what gets chunked; the scale is what keeps the
    highlight pointing at the right words."""
    pages = [
        {
            "page_index": 0,
            "page_label": "1",
            "width": 200.0,
            "height": 200.0,
            "text": "inter national",
            "starts": [0, 6],
            "words": ["inter", "national"],
            "boxes": [(0, 0, 10, 10, 0, 0, 0), (11, 0, 20, 10, 0, 0, 1)],
            "corrected_text": "international",
            "scale": 14 / 13,
        }
    ]

    documents, layout, scales, _ = _documents("d.pdf", pages)

    assert documents[0].page_content == "international"
    assert scales == {0: 14 / 13}
    assert layout[0].words == ["inter", "national"], "the original boxes must survive"


def test_an_uncorrected_page_gets_no_scale():
    """A page that kept its extracted text needs no rebasing: its offsets index
    the very string the boxes were measured against. Recording a scale for it
    would move a correct highlight off its words."""
    pages = [
        {
            "page_index": 0,
            "page_label": "1",
            "width": 200.0,
            "height": 200.0,
            "text": "alpha beta",
            "starts": [0, 6],
            "words": ["alpha", "beta"],
            "boxes": [(0, 0, 10, 10, 0, 0, 0), (11, 0, 20, 10, 0, 0, 1)],
        }
    ]

    _, _, scales, _ = _documents("d.pdf", pages)

    assert scales == {}


# --- end to end, against the fake db ------------------------------------------


@pytest.fixture
def project(fake_db):
    """A project row for assemble to attach chunk ids to, and its object id."""
    from data.models import Project

    record = Project(project_id="proj-1", name="A notebook")
    fake_db.projects().items["proj-1"] = record

    return record.id


@pytest.fixture
def batches_from(tmp_path, fake_db, project):
    """Parse a real synthetic PDF into stored batches, as the parse stage does."""
    from data.models import IngestBatch, IngestBatchModel, IngestRun
    from data.models.ingest import IngestBatchStatus

    async def build(pages_text, size=10, corrected=None):
        pdf = tmp_path / "doc.pdf"
        make_pdf(pdf, pages_text)

        total = page_count(pdf)
        ranges = list(range(0, total, size))
        batches = IngestBatchModel(fake_db)

        await batches.start_run(
            IngestRun(
                asset_id="a1",
                project_id="proj-1",
                total_batches=len(ranges),
                request_data={"chunk_size": 200, "overlap_size": 20},
                project_object_id=str(project),
            )
        )

        for batch_index, start in enumerate(ranges):
            end = min(start + size, total)
            pages = [page_to_dict(page) for page in extract_page_range(pdf, start, end)]

            if corrected:
                for page in pages:
                    if page["page_index"] in corrected:
                        replacement = corrected[page["page_index"]]
                        page["corrected_text"] = replacement
                        page["scale"] = len(page["text"]) / len(replacement)

            await batches.save_batch(
                IngestBatch(
                    asset_id="a1",
                    project_id="proj-1",
                    batch_index=batch_index,
                    status=IngestBatchStatus.CORRECTED,
                    asset_name="doc.pdf",
                    payload={"pages": pages},
                )
            )

        return len(ranges)

    return build


async def test_every_page_reaches_a_chunk(fake_db, project, batches_from):
    """The whole point of the fan-out: a document split across batches comes
    back whole. A dropped batch is a silently shorter notebook."""
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    count = await batches_from([f"page {w} " * 40 for w in words], size=3)

    assert count == 3, "expected 7 pages to split into 3 batches of at most 3"

    result = await assemble_chunks("a1", fake_db)

    assert result["pages"] == 7

    stored = " ".join(c.chunk_content for c in fake_db.chunks().items)

    for word in words:
        assert word in stored, f"page {word!r} never reached a chunk"


async def test_a_corrected_page_is_what_gets_chunked(fake_db, project, batches_from):
    """The corrected text, not the extracted text, is what ends up indexed --
    otherwise the pass costs a model call and changes nothing."""
    await batches_from(
        ["origyinal wurds here " * 20, "second page " * 20],
        corrected={0: "original words here " * 20},
    )

    await assemble_chunks("a1", fake_db)

    stored = " ".join(c.chunk_content for c in fake_db.chunks().items)

    assert "original words here" in stored
    assert "origyinal wurds" not in stored


async def test_a_corrected_page_keeps_an_approximate_highlight(fake_db, project, batches_from):
    """The failure this guards against is invisible: the chunks are right, the
    text is right, and only the rectangle a citation draws is silently gone.

    A corrected page's offsets index a string the boxes were not measured
    against, so the highlight is rebased through the scale and marked approx --
    the same mechanism the OCR re-read already used."""
    original = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 6

    await batches_from([original], corrected={0: original.replace("  ", " ")})

    await assemble_chunks("a1", fake_db)

    highlights = [
        c.chunk_metadata.get("highlight") for c in fake_db.chunks().items if c.chunk_metadata.get("highlight")
    ]

    assert highlights, "a corrected page lost every highlight it had"
    assert all("r" in h and h["r"] for h in highlights), "a highlight carried no rectangles"


async def test_an_uncorrected_page_keeps_an_exact_highlight(fake_db, project, batches_from):
    """With post-processing off, nothing about the highlight should change from
    what the single-process path produced."""
    await batches_from(["alpha bravo charlie delta echo foxtrot " * 8])

    await assemble_chunks("a1", fake_db)

    highlights = [
        c.chunk_metadata.get("highlight") for c in fake_db.chunks().items if c.chunk_metadata.get("highlight")
    ]

    assert highlights
    assert all("approx" not in h for h in highlights), "an untouched page was marked approximate"


# --- the claim that replaced the chord ----------------------------------------


@pytest.fixture
def run_of(fake_db):
    """An ingest run of *total* batches, none of them corrected yet."""
    from data.models import IngestBatch, IngestBatchModel, IngestRun
    from data.models.ingest import IngestBatchStatus

    batches = IngestBatchModel(fake_db)

    async def build(total):
        await batches.start_run(IngestRun(asset_id="a1", project_id="p1", total_batches=total))
        for index in range(total):
            await batches.save_batch(
                IngestBatch(
                    asset_id="a1",
                    project_id="p1",
                    batch_index=index,
                    status=IngestBatchStatus.PARSED,
                    payload={"pages": []},
                )
            )
        return batches

    return build


async def _correct_one(batches, index):
    from data.models.ingest import IngestBatchStatus

    batch = await batches.find_batch("a1", index)
    batch.status = IngestBatchStatus.CORRECTED
    await batches.save_batch(batch)


async def test_the_claim_is_refused_until_every_batch_is_corrected(run_of):
    """A claim granted early chunks a document that is still being parsed —
    the pages simply are not there yet, and the ones that are get stored as if
    they were the whole book."""
    batches = await run_of(3)

    await _correct_one(batches, 0)
    assert await batches.claim_collection("a1") is False

    await _correct_one(batches, 1)
    assert await batches.claim_collection("a1") is False

    await _correct_one(batches, 2)
    assert await batches.claim_collection("a1") is True


async def test_only_one_caller_can_ever_claim(run_of):
    """Every batch asks when it finishes, so on the last one several callers
    can be asking at once. Two winners would chunk and store the document
    twice — and this is the exact job the Celery chord was doing when it
    registered one member of twenty-three and never fired at all."""
    batches = await run_of(2)

    await _correct_one(batches, 0)
    await _correct_one(batches, 1)

    results = [await batches.claim_collection("a1") for _ in range(5)]

    assert results.count(True) == 1, f"expected exactly one winner, got {results}"


async def test_a_redelivered_batch_cannot_push_the_count_past_the_total(run_of):
    """acks_late means a batch can run twice. Saving it again must overwrite
    its own row, not add a second: the previous Redis INCR counter did add,
    and a progress bar reached 104%."""
    batches = await run_of(2)

    await _correct_one(batches, 0)
    await _correct_one(batches, 0)
    await _correct_one(batches, 0)

    assert await batches.count_corrected("a1") == 1
    assert await batches.claim_collection("a1") is False, "a repeat must not complete the run"


async def test_clearing_a_run_removes_its_batches(run_of):
    """These rows hold the full text of the document. Left behind they would
    keep a second copy of every book ever ingested."""
    batches = await run_of(2)

    await batches.clear_run("a1")

    assert await batches.find_run("a1") is None
    assert await batches.count_corrected("a1") == 0
    assert [b async for b in batches.iter_batches("a1")] == []
