"""Per-chat model choice and generation settings, including highlight_color."""

import pytest


async def test_get_chat_reports_the_default_highlight_color(client, seed):
    """No .env-wide fallback exists for this one — a fresh chat still needs a
    real value, unlike temperature or chunk_size which report a setting."""
    response = await client.get("/chat/chats/c1")

    assert response.json()["highlight_color"] == "#FFFF00"


async def test_settings_patch_changes_the_highlight_color(client, seed):
    response = await client.patch(
        "/chat/chats/c1/settings", json={"highlight_color": "#00FF00"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["highlight_color"] == "#00FF00"
    assert body["applied"] == ["highlight_color"]


async def test_the_stored_color_survives_a_reread(client, seed):
    await client.patch("/chat/chats/c1/settings", json={"highlight_color": "#00FF00"})

    response = await client.get("/chat/chats/c1")

    assert response.json()["highlight_color"] == "#00FF00"


@pytest.mark.parametrize("bad", ["yellow", "#fff", "#gggggg", "00ff00", "#00FF0"])
async def test_a_non_hex_color_is_rejected(client, seed, bad):
    response = await client.patch(
        "/chat/chats/c1/settings", json={"highlight_color": bad}
    )

    assert response.status_code == 422


async def test_changing_the_color_does_not_touch_other_settings(client, seed):
    await client.patch("/chat/chats/c1/settings", json={"temperature": 0.9})

    response = await client.patch(
        "/chat/chats/c1/settings", json={"highlight_color": "#0000FF"}
    )

    body = response.json()
    assert body["highlight_color"] == "#0000FF"
    assert body["temperature"] == 0.9


async def test_settings_404s_for_an_unknown_chat(client, seed):
    response = await client.patch(
        "/chat/chats/nope/settings", json={"highlight_color": "#00FF00"}
    )

    assert response.status_code == 404


# --- the id a notebook reports is the id the catalogue lists -------------------


async def test_a_notebook_with_no_model_reports_the_qualified_default(client, seed, monkeypatch):
    """The picker matches on an exact id. This route used to answer with the
    raw .env value while the catalogue answered with a qualified one, and for
    an NVIDIA default those are different strings — which the UI rendered as
    "Missing" against a notebook that was working fine."""
    monkeypatch.setenv("EMBEDDING_BACKEND", "nvidia")
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "nvidia/nemotron-3-embed-1b")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    from utils import get_settings

    get_settings.cache_clear()

    response = await client.get("/chat/chats/c1")

    assert response.status_code == 200
    assert response.json()["embedding_model"] == "nvidia/nvidia/nemotron-3-embed-1b"


async def test_that_default_matches_what_the_catalogue_calls_current(client, seed, monkeypatch):
    """Both sides through one helper, so they cannot drift apart again."""
    monkeypatch.setenv("EMBEDDING_BACKEND", "nvidia")
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "nvidia/nemotron-3-embed-1b")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    from controllers import ModelController
    from utils import get_settings

    get_settings.cache_clear()

    reported = (await client.get("/chat/chats/c1")).json()["embedding_model"]
    catalogue = await ModelController().catalogue(probe_embeddings=False)

    assert reported == catalogue["current"]["embedding"]
