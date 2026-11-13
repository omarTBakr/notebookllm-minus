"""What the Visualization view shows: the operations tools, framed inside the app.

Grafana, Flower and RabbitMQ's management UI are served by nginx under
sub-paths of the app's own origin, behind the app's sign-in. Langfuse cannot
be served under a sub-path (its image has no base-path build), so it is
framed from its own origin and keeps its own sign-in.

This builds the tab list from settings and nothing else, so what a deployment
exposes is decided in one env file: an empty URL hides that tab, and
VISUALIZATION_ENABLED off hides the whole view.
"""

from __future__ import annotations

#: The provisioned Grafana dashboards (Docker/services/grafana/dashboards/),
#: as (id, dashboard uid, i18n key). The uid is the "uid" field of each JSON
#: file; tests/domain/test_visualization.py checks the two against each other.
GRAFANA_DASHBOARDS: tuple[tuple[str, str, str], ...] = (
    ("app", "notebookllm-fastapi", "vizDashApp"),
    ("queues", "notebookllm-rabbitmq", "vizDashQueues"),
    ("database", "notebookllm-postgres", "vizDashDatabase"),
    ("host", "notebookllm-node", "vizDashHost"),
)


def _base(url: str) -> str:
    """*url* with exactly one trailing slash, so a path can be appended."""
    return url.strip().rstrip("/") + "/"


def visualization_tabs(settings) -> list[dict]:
    """The tabs to offer, in order. Empty when the view is switched off.

    Each tab is ``{id, label, url}``; ``label`` is an i18n key the page
    translates. The dashboards tab also carries ``views`` (one per Grafana
    dashboard), and the traces tab ``session_url``, a template with
    ``{chat_id}`` in it: a notebook's id is its Langfuse session id, so the
    page can open straight onto the notebook that is on screen.
    """
    if not settings.VISUALIZATION_ENABLED:
        return []

    tabs: list[dict] = []

    if settings.VISUALIZATION_GRAFANA_URL.strip():
        grafana = _base(settings.VISUALIZATION_GRAFANA_URL)
        # kiosk hides Grafana's own navigation: inside the app there is
        # already a header, and two would leave little room for the panels.
        views = [
            {"id": view_id, "label": label, "url": f"{grafana}d/{uid}?kiosk"}
            for view_id, uid, label in GRAFANA_DASHBOARDS
        ]
        tabs.append({"id": "dashboards", "label": "vizDashboards", "url": views[0]["url"], "views": views})

    if settings.VISUALIZATION_LANGFUSE_URL.strip():
        langfuse = _base(settings.VISUALIZATION_LANGFUSE_URL)
        project = f"{langfuse}project/{settings.VISUALIZATION_LANGFUSE_PROJECT_ID}"
        tabs.append(
            {
                "id": "traces",
                "label": "vizTraces",
                "url": f"{project}/traces",
                "session_url": f"{project}/sessions/{{chat_id}}",
            }
        )

    if settings.VISUALIZATION_FLOWER_URL.strip():
        tabs.append({"id": "tasks", "label": "vizTasks", "url": f"{_base(settings.VISUALIZATION_FLOWER_URL)}tasks"})

    if settings.VISUALIZATION_RABBITMQ_URL.strip():
        tabs.append(
            {"id": "queues", "label": "vizQueues", "url": f"{_base(settings.VISUALIZATION_RABBITMQ_URL)}#/queues"}
        )

    return tabs
