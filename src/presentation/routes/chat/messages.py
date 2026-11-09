"""Message listing and the streaming answer endpoint."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from presentation import dependencies as deps
from shared.exceptions import InvalidInputError, NotebookLLMError
from shared.utils import request_id_ctx, tracing

from ..schemas import MessageRequest
from ._helpers import _sse, logger
from .memory import handle_memory_command, is_memory_command

messages_router = APIRouter()


@messages_router.get("/chats/{chat_id}/messages")
async def list_messages(chat_id: str, http_request: Request):
    messages = await deps.messages(http_request).transcript(chat_id)

    return JSONResponse(status_code=200, content={"chat_id": chat_id, "messages": messages})


def _decode_picture(data_url: str, max_bytes: int, max_side: int) -> tuple[bytes, str]:
    """A composer data URL as checked, scaled ``(bytes, mime)``.

    The declared type in the URL is ignored: the bytes are sniffed, decoded
    and re-encoded by shared.utils.images, so nothing a client claims about
    the picture is trusted.
    """
    import base64
    import binascii

    from shared.utils.images import InvalidImageError, prepare

    header, _, payload = data_url.partition(",")
    if not header.startswith("data:image/") or ";base64" not in header or not payload:
        raise InvalidInputError("The attached image must be a base64 data URL")

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputError("The attached image is not valid base64") from exc

    if len(raw) > max_bytes:
        raise InvalidInputError(f"The attached image is larger than {max_bytes // (1024 * 1024)} MB")

    try:
        prepared = prepare(raw, max_side=max_side)
    except InvalidImageError as exc:
        raise InvalidInputError(f"The attached image could not be used: {exc}") from exc

    return prepared.data, prepared.mime


@messages_router.post("/chats/{chat_id}/message")
async def send_message(chat_id: str, request: MessageRequest, http_request: Request):
    """Ask a question; stream the answer back as server-sent events.

    Frames: one ``meta`` (grounded flag + citations), then ``delta`` per piece
    of text, then ``done``. A complex question also sends ``step`` frames
    (the plan, each part as it is answered, then the start of the combined
    answer); see ChatService.answer_stream. With guardrails on, a refused
    question or a withheld passage sends a ``guardrail`` frame with the
    refusal text in place of the answer.
    """
    from shared.utils import get_settings

    settings = get_settings()
    service = deps.messages(http_request)
    chat = await service.get_chat(chat_id)

    logger.debug("Message in chat %r (lang=%s)", chat_id, chat.lang)

    # History must be read *before* the new question is stored, or the question
    # arrives in the model's context twice — once as history, once as the prompt.
    history = await service.recent_history(chat_id, settings.CHAT_HISTORY_LIMIT)

    # A picture from the composer: checked before anything is stored, so a
    # bad upload is a 400 and leaves no orphan question in the transcript.
    picture: tuple[bytes, str] | None = None
    picture_caption: str | None = None
    if request.image:
        if not settings.MULTIMODAL_ENABLED:
            raise InvalidInputError("Image questions are switched off (MULTIMODAL_ENABLED)")
        picture = _decode_picture(request.image, settings.IMAGE_QUERY_MAX_BYTES, settings.IMAGE_MAX_SIDE_PX)

        from application.services.ingest.ImageCaptionService import caption_picture

        captioner = deps.caption_client(http_request)
        if captioner is not None:
            picture_caption = await caption_picture(captioner, picture[0], picture[1], chat.lang or "en")

    # What is stored is text: the question, plus what the picture showed.
    # The pixels are not kept, so a later turn knows the picture by caption.
    stored_text = request.text
    if picture is not None:
        from application.prompts.template_parser import TemplateParser

        note = TemplateParser(lang=chat.lang, default_lang=settings.DEFAULT_LANG).get(
            "rag", "user_image_note", {"caption": picture_caption or "-"}
        )
        stored_text = f"{request.text}\n{note}".strip()

    await service.add_user_message(chat_id, stored_text)

    memory_text = is_memory_command(request.text) if picture is None else None

    if memory_text is not None:
        return await handle_memory_command(http_request, chat, service, memory_text)

    await service.name_chat_from_question(chat, history, request.text or stored_text)

    vision = False
    if settings.MULTIMODAL_ENABLED:
        from application.services.llm.vision import has_vision

        vision = await has_vision(getattr(chat, "generation_model", None))

    controller = deps.chat_service(http_request, chat, vision=vision)
    top_k = request.top_k or settings.RETRIEVAL_TOP_K

    source_names = await service.source_names(chat_id)

    # None means "search everything"; a list narrows it. Resolved here rather
    # than in the controller so the stored exclusions stay a route concern.
    selected_assets = None

    if chat.excluded_assets:
        excluded = set(chat.excluded_assets)
        selected_assets = [asset_id for asset_id in source_names if asset_id not in excluded]

    async def events():
        # One Langfuse trace per answer: user and notebook become the trace's
        # user and session, so the UI can show a whole conversation together.
        with tracing.trace_attributes(
            user_id=str(chat.user_id) if chat.user_id else None,
            session_id=str(chat_id),
            trace_name="chat.answer",
            tags=["chat", chat.lang or settings.DEFAULT_LANG],
            metadata={"request_id": request_id_ctx.get()},
        ):
            with tracing.observation("chat.answer", as_type="agent", input={"question": request.text}) as root:
                answer: list[str] = []
                async for frame in _answer_frames(answer):
                    yield frame
                root.update(output="".join(answer))

    async def _answer_frames(answer: list[str]):
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
                image=picture,
                image_caption=picture_caption,
                image_lookup=service.image_lookup() if settings.MULTIMODAL_ENABLED else None,
            ):
                if event["type"] == "meta":
                    citations = event["citations"]
                elif event["type"] == "delta":
                    answer.append(event["text"])
                elif event["type"] == "guardrail":
                    # The refusal is what the reader saw, so it is what is
                    # stored: on its own for a refused question, after the
                    # passages already shown for a withheld answer.
                    answer.append(("\n\n" if answer else "") + event["message"])

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
