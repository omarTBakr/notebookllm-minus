"""Message listing and the streaming answer endpoint."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from enums import ChatRole
from exceptions import NotebookLLMError
from models import AssetModel, ChatModel, Message, MessageModel

from ..schemas import MessageRequest
from ._helpers import _new_id, _chat_controller, _sse, logger
from ._pages import keys_from_hits, resolve_pages

messages_router = APIRouter()


@messages_router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str, http_request: Request):

    db = http_request.app.db

    await ChatModel(db).get_chat(chat_id)

    # Citations were frozen with whatever the source was called when the
    # answer was written. Renaming a source has to reach old answers too, or
    # the transcript keeps citing a name that no longer exists anywhere.
    source_names = {
        asset.asset_id: asset.name
        async for asset in AssetModel(db).iter_assets_for_projects([chat_id])
    }

    stored = [m async for m in MessageModel(db).iter_chat_messages(chat_id)]

    # Answers written before citations carried a page have none stored. Fill
    # those in from the chunks they name, so an existing notebook gets clickable
    # citations without being re-indexed.
    #
    # Only where absent, and that asymmetry with the rename above is the point:
    # a rename is retroactively true of an old answer, but re-processing a
    # document remaps chunk_order onto different pages, so back-filling a
    # message that already has a page would silently move the citation to a
    # page that answer never read.
    missing = [
        (cite.get("asset_id"), cite.get("chunk_order"))
        for message in stored
        for cite in message.citations
        if cite.get("page_number") is None
        and cite.get("asset_id") is not None
        and cite.get("chunk_order") is not None
    ]
    pages = await resolve_pages(db, missing)

    def resolved(citations: list[dict]) -> list[dict]:
        out = []

        for cite in citations:
            fresh = {
                **cite,
                "source": source_names.get(cite.get("asset_id")) or cite.get("source"),
            }

            if fresh.get("page_number") is None:
                located = pages.get((cite.get("asset_id"), cite.get("chunk_order")))
                if located:
                    fresh.update(located)

            out.append(fresh)

        return out

    messages = [
        {
            "message_id": m.message_id,
            "role": m.role.value,
            "content": m.content,
            "citations": resolved(m.citations),
            "created_at": m.created_at.isoformat(),
        }
        for m in stored
    ]

    return JSONResponse(
        status_code=200, content={"chat_id": chat_id, "messages": messages}
    )


@messages_router.post("/chats/{chat_id}/message")
async def send_message(chat_id: str, request: MessageRequest, http_request: Request):
    """Ask a question; stream the answer back as server-sent events.

    Frames: one ``meta`` (grounded flag + citations), then ``delta`` per piece
    of text, then ``done``.
    """
    from utils import get_settings

    settings = get_settings()
    db = http_request.app.db

    chat = await ChatModel(db).get_chat(chat_id)
    message_model = MessageModel(db)

    logger.debug("Message in chat %r (lang=%s)", chat_id, chat.lang)

    # History must be read *before* the new question is stored, or the question
    # arrives in the model's context twice — once as history, once as the prompt.
    history = await message_model.get_recent_history(chat_id, settings.CHAT_HISTORY_LIMIT)

    await message_model.create_message(
        Message(
            message_id=_new_id(),
            chat_id=chat_id,
            role=ChatRole.USER,
            content=request.text,
        )
    )

    # Name the chat after its first question, so the sidebar isn't a column of
    # "New chat".
    if not history:
        title = request.text.strip()[:60]
        if title:
            await ChatModel(db).rename(chat_id, title)

    controller = _chat_controller(http_request, chat)
    top_k = request.top_k or settings.RETRIEVAL_TOP_K

    # One pass over this chat's assets answers two questions: which ones a
    # search may touch, and what each is currently called. file_bytes is
    # projected out of this iterator, so the extra use costs nothing.
    source_names = {
        asset.asset_id: asset.name
        async for asset in AssetModel(db).iter_assets_for_projects([chat_id])
    }

    # None means "search everything"; a list narrows it. Resolved here rather
    # than in the controller so the stored exclusions stay a route concern.
    selected_assets = None

    if chat.excluded_assets:
        excluded = set(chat.excluded_assets)
        selected_assets = [
            asset_id for asset_id in source_names if asset_id not in excluded
        ]

    async def events():
        answer: list[str] = []
        citations: list[dict] = []

        try:
            async for event in controller.answer_stream(
                chat_id=chat_id,
                question=request.text,
                lang=chat.lang,
                history=history,
                top_k=top_k,
                temperature=chat.temperature,
                max_tokens=chat.max_tokens,
                asset_ids=selected_assets,
                source_names=source_names,
                # Resolves each hit to a page so the citation can link to it.
                page_lookup=lambda hits: resolve_pages(db, keys_from_hits(hits)),
            ):
                if event["type"] == "meta":
                    citations = event["citations"]
                elif event["type"] == "delta":
                    answer.append(event["text"])

                yield _sse(event)

        except NotebookLLMError as exc:
            # The response has already started, so this cannot become an HTTP
            # error code — the status line went out with the first byte. The
            # failure is reported in-band and logged here, the one place that
            # departs from "raise and let the handler log it".
            logger.warning("Streaming answer failed for chat %r: %s", chat_id, exc)
            yield _sse({"type": "error", "detail": str(exc)})

        except Exception:
            logger.exception("Unexpected failure streaming chat %r", chat_id)
            yield _sse({"type": "error", "detail": "Internal server error"})

        finally:
            # Persist whatever was produced. A half-finished answer is still
            # worth keeping: the user saw it, so it should survive a reload.
            #
            # Shielded, because the most common way to reach this block is now
            # the reader pressing Stop. That aborts the fetch, Starlette
            # cancels the task pumping this generator, and CancelledError is
            # thrown in at the yield above. Cancellation stays pending, so a
            # bare await here is liable to be cancelled again before the insert
            # lands — the answer the reader chose to keep would be the one most
            # likely to be lost. shield lets the write finish.
            text = "".join(answer)
            if text:
                await asyncio.shield(
                    message_model.create_message(
                        Message(
                            message_id=_new_id(),
                            chat_id=chat_id,
                            role=ChatRole.ASSISTANT,
                            content=text,
                            citations=citations,
                        )
                    )
                )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx buffering the stream if this ever sits behind one.
            "X-Accel-Buffering": "no",
        },
    )
