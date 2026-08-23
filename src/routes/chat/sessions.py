"""Session routes: create, list; plus the default_session helper."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models import Session, SessionModel, UserModel

from ..schemas import CreateSessionRequest
from ._helpers import _new_id

sessions_router = APIRouter()


async def default_session(db, user_id: str) -> str:
    """The session a notebook is filed under.

    Notebooks are the UI's unit of work; sessions are a layer the interface no
    longer shows. Rather than change the schema, every profile keeps one
    implicit session and notebooks hang off it — so Chat.session_id stays a
    real foreign key and nothing had to be migrated.
    """
    session_model = SessionModel(db)

    async for session in session_model.iter_user_sessions(user_id):
        return session.session_id

    session = Session(session_id=_new_id(), user_id=user_id, title="Default")
    await session_model.create_session(session)

    return session.session_id


@sessions_router.post("/users/{user_id}/sessions")
async def create_session(user_id: str, request: CreateSessionRequest, http_request: Request):

    await UserModel(http_request.app.db).get_user(user_id)

    session = Session(session_id=_new_id(), user_id=user_id, title=request.title)

    await SessionModel(http_request.app.db).create_session(session)

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

    await UserModel(http_request.app.db).get_user(user_id)

    sessions = [
        {
            "session_id": s.session_id,
            "title": s.title,
            "created_at": s.created_at.isoformat(),
        }
        async for s in SessionModel(http_request.app.db).iter_user_sessions(user_id)
    ]

    return JSONResponse(status_code=200, content={"user_id": user_id, "sessions": sessions})
