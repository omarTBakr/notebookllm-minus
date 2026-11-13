"""Which operations tools the Visualization view may show."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from application.services.core.visualization import visualization_tabs
from shared.utils import get_settings

visualization_router = APIRouter()


@visualization_router.get("/visualization")
async def visualization():
    """The tabs for the Visualization view: Grafana, Langfuse, Flower, RabbitMQ.

    Only addresses, never data: the tools are framed by the browser, and each
    is guarded where it is served (nginx checks the session for the three
    under this origin; Langfuse has its own sign-in). ``enabled`` false means
    the button stays hidden.
    """
    tabs = visualization_tabs(get_settings())

    return JSONResponse(status_code=200, content={"enabled": bool(tabs), "tabs": tabs})
