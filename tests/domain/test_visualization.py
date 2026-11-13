"""Which operations tools the Visualization view offers, and at what address."""

import json
from pathlib import Path
from types import SimpleNamespace

from application.services.core.visualization import (
    GRAFANA_DASHBOARDS,
    visualization_tabs,
)

DASHBOARDS = Path(__file__).resolve().parents[2] / "Docker" / "services" / "grafana" / "dashboards"


def settings(**overrides):
    values = {
        "VISUALIZATION_ENABLED": True,
        "VISUALIZATION_GRAFANA_URL": "/grafana/",
        "VISUALIZATION_FLOWER_URL": "/flower/",
        "VISUALIZATION_RABBITMQ_URL": "/rabbitmq/",
        "VISUALIZATION_LANGFUSE_URL": "http://localhost:3001",
        "VISUALIZATION_LANGFUSE_PROJECT_ID": "notebookllm-minus",
    }
    return SimpleNamespace(**(values | overrides))


def test_switched_off_there_is_nothing_to_show():
    assert visualization_tabs(settings(VISUALIZATION_ENABLED=False)) == []


def test_the_four_tools_in_order():
    tabs = visualization_tabs(settings())

    assert [tab["id"] for tab in tabs] == ["dashboards", "traces", "tasks", "queues"]
    assert [tab["label"] for tab in tabs] == ["vizDashboards", "vizTraces", "vizTasks", "vizQueues"]


def test_an_empty_address_hides_that_tab_only():
    tabs = visualization_tabs(settings(VISUALIZATION_LANGFUSE_URL="  ", VISUALIZATION_RABBITMQ_URL=""))

    assert [tab["id"] for tab in tabs] == ["dashboards", "tasks"]


def test_dashboards_are_framed_without_grafanas_own_navigation():
    dashboards = visualization_tabs(settings())[0]

    assert dashboards["url"] == dashboards["views"][0]["url"]
    assert [view["url"] for view in dashboards["views"]] == [
        "/grafana/d/notebookllm-fastapi?kiosk",
        "/grafana/d/notebookllm-rabbitmq?kiosk",
        "/grafana/d/notebookllm-postgres?kiosk",
        "/grafana/d/notebookllm-node?kiosk",
    ]


def test_every_listed_dashboard_is_one_grafana_is_provisioned_with():
    """A uid that matches no file opens Grafana's "dashboard not found"."""
    provisioned = {json.loads(path.read_text())["uid"] for path in DASHBOARDS.glob("*.json")}

    assert {uid for _, uid, _ in GRAFANA_DASHBOARDS} == provisioned


def test_traces_carry_a_template_for_the_open_notebook():
    traces = visualization_tabs(settings())[1]

    assert traces["url"] == "http://localhost:3001/project/notebookllm-minus/traces"
    assert traces["session_url"] == "http://localhost:3001/project/notebookllm-minus/sessions/{chat_id}"


def test_a_missing_or_doubled_trailing_slash_makes_no_difference():
    tabs = visualization_tabs(settings(VISUALIZATION_FLOWER_URL="/flower", VISUALIZATION_RABBITMQ_URL="/rabbitmq//"))

    assert tabs[2]["url"] == "/flower/tasks"
    assert tabs[3]["url"] == "/rabbitmq/#/queues"
