from typing import Protocol


class Upload(Protocol):
    """What the services need from an uploaded file.

    Structural, so a FastAPI ``UploadFile`` satisfies it without the services
    importing the web framework.
    """

    filename: str | None
    content_type: str | None
    size: int | None

    async def read(self, size: int = -1) -> bytes: ...
