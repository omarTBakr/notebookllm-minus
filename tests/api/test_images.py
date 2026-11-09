"""Pictures through the API: uploads, the composer, and the crop endpoint."""

import base64
import io

import pytest
from PIL import Image

from shared.utils import get_settings

IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"]


def png(width=300, height=200, colour=(30, 120, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def multimodal(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "MULTIMODAL_ENABLED", True)
    monkeypatch.setattr(settings, "ALLOWED_TYPES", [*settings.ALLOWED_TYPES, *IMAGE_TYPES])
    return settings


# --- uploads ---------------------------------------------------------------------------


async def test_an_image_upload_is_refused_while_multimodal_is_off(ingest, client, seed, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ALLOWED_TYPES", [*settings.ALLOWED_TYPES, *IMAGE_TYPES])

    response = await ingest("c1", {"file": ("chart.png", png(), "image/png")})

    assert response.status_code == 400


async def test_an_uploaded_picture_becomes_one_image_chunk(ingest, client, seed, fake_db, multimodal):
    response = await ingest("c1", {"file": ("chart.png", png(), "image/png")})

    assert response.status_code in (200, 201, 202), response.text
    asset = next(a for a in fake_db.assets().items.values() if a.name == "chart.png")
    assert asset.asset_type.value == "image"

    images = [c for c in fake_db.chunks().items if (c.chunk_metadata or {}).get("kind") == "image"]
    assert len(images) == 1
    meta = images[0].chunk_metadata
    assert meta["whole_page"] is True and meta["bbox"] == [0.0, 0.0, 300.0, 200.0]
    assert images[0].chunk_content == "[image, page 1]"  # no caption model under test


async def test_a_text_file_claiming_to_be_an_image_is_refused(ingest, client, seed, multimodal):
    response = await ingest("c1", {"file": ("fake.png", b"just some text", "image/png")})

    assert response.status_code == 400


async def test_the_content_endpoint_serves_an_image_as_itself(ingest, client, seed, fake_db, multimodal):
    await ingest("c1", {"file": ("chart.png", png(), "image/png")})
    asset = next(a for a in fake_db.assets().items.values() if a.name == "chart.png")

    response = await client.get(f"/chat/chats/c1/assets/{asset.asset_id}/content")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_the_crop_endpoint_returns_the_picture_scaled(ingest, client, seed, fake_db, multimodal):
    await ingest("c1", {"file": ("chart.png", png(1200, 800), "image/png")})
    asset = next(a for a in fake_db.assets().items.values() if a.name == "chart.png")
    chunk = next(c for c in fake_db.chunks().items if c.asset_id == asset.asset_id)

    response = await client.get(
        f"/chat/chats/c1/assets/{asset.asset_id}/chunks/{chunk.chunk_order}/image", params={"size": 240}
    )

    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).size == (240, 160)


async def test_the_crop_endpoint_404s_for_a_text_chunk(ingest, client, seed, fake_db):
    await ingest("c1", {"file": ("note.txt", b"some words here " * 30, "text/plain")})
    asset = next(a for a in fake_db.assets().items.values() if a.name == "note.txt")
    chunk = next(c for c in fake_db.chunks().items if c.asset_id == asset.asset_id)

    response = await client.get(f"/chat/chats/c1/assets/{asset.asset_id}/chunks/{chunk.chunk_order}/image")

    assert response.status_code == 404


async def test_locate_reports_the_chunk_kind(ingest, client, seed, fake_db, multimodal):
    await ingest("c1", {"file": ("chart.png", png(), "image/png")})
    asset = next(a for a in fake_db.assets().items.values() if a.name == "chart.png")
    chunk = next(c for c in fake_db.chunks().items if c.asset_id == asset.asset_id)

    body = (await client.get(f"/chat/chats/c1/assets/{asset.asset_id}/chunks/{chunk.chunk_order}/locate")).json()

    assert body["kind"] == "image"
    assert body["highlight"]["r"] == [[0.0, 0.0, 300.0, 200.0]]


# --- the composer ----------------------------------------------------------------------------


def data_url(data: bytes, mime="image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


async def test_an_empty_question_without_a_picture_is_rejected(client, seed):
    response = await client.post("/chat/chats/c1/message", json={"text": "  "})

    assert response.status_code == 422


async def test_a_picture_question_is_refused_while_multimodal_is_off(client, seed):
    response = await client.post("/chat/chats/c1/message", json={"text": "what is this", "image": data_url(png())})

    assert response.status_code == 400


async def test_a_composer_picture_that_is_not_an_image_is_a_400(client, seed, multimodal):
    response = await client.post(
        "/chat/chats/c1/message", json={"text": "what is this", "image": data_url(b"not a picture")}
    )

    assert response.status_code == 400


async def test_a_composer_picture_over_the_limit_is_a_400(client, seed, multimodal, monkeypatch):
    monkeypatch.setattr(multimodal, "IMAGE_QUERY_MAX_BYTES", 100)

    response = await client.post("/chat/chats/c1/message", json={"text": "what is this", "image": data_url(png())})

    assert response.status_code == 400


async def test_a_picture_question_streams_and_stores_a_text_note(client, seed, fake_db, multimodal):
    response = await client.post("/chat/chats/c1/message", json={"text": "", "image": data_url(png())})

    assert response.status_code == 200
    assert '"type": "done"' in response.text or '"type":"done"' in response.text
    stored = [m for m in fake_db.messages().items if m.chat_id == "c1" and m.role.value == "user"]
    # No caption model under test, so the note has a placeholder caption; the
    # pixels themselves are never stored.
    assert stored[-1].content.startswith("[Attached image:")
