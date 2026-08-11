from fastapi import APIRouter, Depends
from utils import get_settings, Settings

base_router = APIRouter(prefix="/base", tags=["base"])


@base_router.get("/health")
async def health_check(app_settings: Settings = Depends(get_settings)):

    application_name = app_settings.APPLICATION_NAME
    app_version = app_settings.APP_VERSION

    return {"application_name": application_name, "app_version": app_version}
