"""Session routes: create, list."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from presentation import dependencies as deps

from ..schemas import CreateSessionRequest

sessions_router = APIRouter()


@sessions_router.post("/users/{user_id}/sessions")
async def create_session(user_id: str, request: CreateSessionRequest, http_request: Request):
    session = await deps.conversations(http_request).create_session(user_id, request.title)

    return JSONResponse(
        status_code=200,
        content={
            "session_id": session.session_id,
            "user_id": user_id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
        },
    )


@sessions_router.get("/users/{user_id}/sessions")
async def list_sessions(user_id: str, http_request: Request):
    sessions = [
        {
            "session_id": s.session_id,
            "title": s.title,
            "created_at": s.created_at.isoformat(),
        }
        for s in await deps.conversations(http_request).list_sessions(user_id)
    ]

    return JSONResponse(status_code=200, content={"user_id": user_id, "sessions": sessions})
