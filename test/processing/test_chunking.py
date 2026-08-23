"""Splitting documents, and the metadata that survives it."""

import pytest
from langchain_core.documents import Document

from controllers import ProcessController
from exceptions import UnsupportedFileTypeError


@pytest.fixture
def controller():
    return ProcessController(chunk_size=50, chunk_overlap=10)


def test_split_file_breaks_a_long_document_up(controller):
    doc = Document(page_content="word " * 200, metadata={"source": "big.txt"})

    chunks = controller.split_file([doc])

    assert len(chunks) > 1


def test_split_file_keeps_the_metadata_on_every_chunk(controller):
    doc = Document(page_content="word " * 200, metadata={"source": "big.txt"})

    chunks = controller.split_file([doc])

    assert all(c.metadata["source"] == "big.txt" for c in chunks)


def test_split_file_leaves_a_short_document_whole(controller):
    doc = Document(page_content="short", metadata={"source": "s.txt"})

    assert [c.page_content for c in controller.split_file([doc])] == ["short"]


def test_split_file_on_nothing_returns_nothing(controller):
    assert controller.split_file([]) == []


def test_process_bytes_reads_plain_text(controller):
    docs = controller.process_bytes(b"hello there", "note1.txt")

    assert "hello there" in "".join(d.page_content for d in docs)


def test_process_bytes_stamps_the_real_filename(controller):
    """The loader writes a temp path into metadata; it is meaningless once the
    temp file is gone, so the asset's own name replaces it."""
    docs = controller.process_bytes(b"hello", "note1.txt")

    assert {d.metadata["source"] for d in docs} == {"note1.txt"}


def test_an_unknown_extension_is_rejected(controller, tmp_path):
    path = tmp_path / "thing.xyz"
    path.write_text("x")

    with pytest.raises(UnsupportedFileTypeError):
        controller.get_loader(path)
