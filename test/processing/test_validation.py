"""Upload validation: type and size."""

import io

import pytest
from fastapi import UploadFile

from controllers import DataController
from exceptions import InvalidFileError


def upload(name="a.txt", content_type="text/plain", size=10):
    file = UploadFile(filename=name, file=io.BytesIO(b"x" * size),
                      headers={"content-type": content_type})
    file.size = size
    return file


@pytest.fixture
def controller():
    return DataController()


def test_a_plain_text_upload_is_accepted(controller):
    controller.validate_file(upload())


def test_a_pdf_upload_is_accepted(controller):
    controller.validate_file(upload(name="a.pdf", content_type="application/pdf"))


def test_an_unlisted_type_is_rejected(controller):
    with pytest.raises(InvalidFileError):
        controller.validate_file(upload(content_type="image/png"))


def test_an_oversized_upload_is_rejected(controller):
    too_big = controller.settings.MAX_FILE_SIZE + 1

    with pytest.raises(InvalidFileError):
        controller.validate_file(upload(size=too_big))


def test_an_unknown_size_is_allowed_through(controller):
    """Starlette leaves .size None for a streamed upload; the read loop is the
    real backstop there."""
    file = upload()
    file.size = None

    controller.validate_file(file)


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN BUG: _validate_file_extension does an exact string match on "
           "the Content-Type header, so a legal parameter such as "
           "'; charset=utf-8' makes a permitted type look forbidden. Same root "
           "cause as AssetType.from_content_type. Remove this marker with the fix.",
)
def test_a_charset_parameter_does_not_make_a_permitted_type_invalid(controller):
    controller.validate_file(upload(content_type="text/plain; charset=utf-8"))
