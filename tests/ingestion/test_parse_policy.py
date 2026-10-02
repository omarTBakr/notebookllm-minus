"""Policies that decide which parsed assets need model correction."""

from types import SimpleNamespace

import pytest

from application.tasks.jobs.ingest.parse import should_skip_correction
from shared.enums import AssetType


@pytest.mark.parametrize(
    "asset_type",
    [AssetType.TEXT, AssetType.MARKDOWN, AssetType.CSV, AssetType.XLSX, AssetType.HTML, AssetType.JSON, AssetType.XML, AssetType.YOUTUBE],
)
def test_text_native_assets_skip_correction(asset_type):
    asset = SimpleNamespace(asset_type=asset_type, source_url="")

    assert should_skip_correction(asset) is True


def test_link_sources_skip_correction_even_when_their_type_is_pdf():
    asset = SimpleNamespace(asset_type=AssetType.PDF, source_url="https://example.test/report.pdf")

    assert should_skip_correction(asset) is True


def test_uploaded_pdf_can_use_correction():
    asset = SimpleNamespace(asset_type=AssetType.PDF, source_url="")

    assert should_skip_correction(asset) is False
