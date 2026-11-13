"""GET /chat/visualization: the tools the top-bar button may open."""

from pathlib import Path

from shared.utils import get_settings

NGINX = Path(__file__).resolve().parents[2] / "Docker" / "services" / "nginx" / "proxy.conf"


async def test_it_needs_a_session(anon_client):
    response = await anon_client.get("/chat/visualization")

    assert response.status_code == 401


async def test_switched_off_the_button_has_nothing_to_open(client, seed, monkeypatch):
    monkeypatch.setattr(get_settings(), "VISUALIZATION_ENABLED", False)

    body = (await client.get("/chat/visualization")).json()

    assert body == {"enabled": False, "tabs": []}


async def test_switched_on_it_lists_the_tools(client, seed, monkeypatch):
    monkeypatch.setattr(get_settings(), "VISUALIZATION_ENABLED", True)

    body = (await client.get("/chat/visualization")).json()

    assert body["enabled"] is True
    assert [tab["id"] for tab in body["tabs"]] == ["dashboards", "traces", "tasks", "queues"]


def test_nginx_serves_each_tool_only_behind_the_apps_session():
    """Grafana and Flower have no sign-in of their own here. If a later edit
    drops `auth_request` from one of these locations, that tool is open to
    anyone who can reach the proxy, and nothing else would notice."""
    text = NGINX.read_text()

    for prefix in ("/grafana/", "/flower/", "/rabbitmq/"):
        start = text.index(f"location {prefix} {{")
        block = text[start : text.index("}", start)]
        assert "auth_request /_auth;" in block, f"{prefix} is not behind the session check"

    gate = text[text.index("location = /_auth {") :]
    gate = gate[: gate.index("}")]
    assert "internal;" in gate and "/auth/me" in gate


def test_the_management_api_url_follows_the_path_prefix(monkeypatch):
    """Flower's Broker tab reads queue depths from this URL; with RabbitMQ's
    management UI under /rabbitmq the API moved with it."""
    settings = get_settings()

    monkeypatch.setattr(settings, "CELERY_MANAGEMENT_PATH_PREFIX", "")
    assert settings.rabbitmq_management_api_url.endswith(f":{settings.CELERY_MANAGEMENT_PORT}/api/")

    monkeypatch.setattr(settings, "CELERY_MANAGEMENT_PATH_PREFIX", "/rabbitmq")
    assert settings.rabbitmq_management_api_url.endswith(f":{settings.CELERY_MANAGEMENT_PORT}/rabbitmq/api/")
