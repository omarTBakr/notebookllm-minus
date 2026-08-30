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
