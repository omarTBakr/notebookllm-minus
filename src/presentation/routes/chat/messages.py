"""Message listing and the streaming answer endpoint."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from presentation import dependencies as deps
from shared.exceptions import NotebookLLMError

from ..schemas import MessageRequest
from ._helpers import _sse, logger
from .memory import handle_memory_command, is_memory_command

messages_router = APIRouter()


@messages_router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str, http_request: Request):
    messages = await deps.messages(http_request).transcript(chat_id)

    return JSONResponse(status_code=200, content={"chat_id": chat_id, "messages": messages})


@messages_router.post("/chats/{chat_id}/message")
async def send_message(chat_id: str, request: MessageRequest, http_request: Request):
    """Ask a question; stream the answer back as server-sent events.

    Frames: one ``meta`` (grounded flag + citations), then ``delta`` per piece
    of text, then ``done``.
    """
    from shared.utils import get_settings

    settings = get_settings()
    service = deps.messages(http_request)
    chat = await service.get_chat(chat_id)

    logger.debug("Message in chat %r (lang=%s)", chat_id, chat.lang)

    # History must be read *before* the new question is stored, or the question
    # arrives in the model's context twice — once as history, once as the prompt.
    history = await service.recent_history(chat_id, settings.CHAT_HISTORY_LIMIT)

    await service.add_user_message(chat_id, request.text)

    memory_text = is_memory_command(request.text)

    if memory_text is not None:
        return await handle_memory_command(http_request, chat, service, memory_text)

    await service.name_chat_from_question(chat, history, request.text)

    controller = deps.chat_service(http_request, chat)
    top_k = request.top_k or settings.RETRIEVAL_TOP_K

    source_names = await service.source_names(chat_id)

    # None means "search everything"; a list narrows it. Resolved here rather
    # than in the controller so the stored exclusions stay a route concern.
    selected_assets = None

    if chat.excluded_assets:
        excluded = set(chat.excluded_assets)
        selected_assets = [asset_id for asset_id in source_names if asset_id not in excluded]

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
                page_lookup=service.page_lookup(),
                user_id=chat.user_id,
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
                await asyncio.shield(service.add_assistant_message(chat_id, text, citations))

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
