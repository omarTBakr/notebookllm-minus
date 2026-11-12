"""Decode, check and scale an image before anything else touches it.

Every image that enters the app (a figure cut out of a PDF, an uploaded
picture, a picture attached to a question) passes through here first, so
the embedder, the caption model and the answering model all receive the same
bounded, normalised bytes:

- the format is read from the bytes themselves, never from a filename or a
  Content-Type header a client chose;
- a decompression bomb (a tiny file that inflates to gigapixels) is refused
  before it is decoded;
- the longest side is scaled down to ``max_side`` and the result re-encoded
  as PNG (lossless, for diagrams and text) or JPEG (photos with no alpha
  channel are an order of magnitude smaller as JPEG).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

#: Formats accepted from outside. GIF is left out: animated, rarely a figure,
#: and no embedder reads more than its first frame.
ACCEPTED_FORMATS: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}

#: Above this many pixels an image is refused unread (about 8k x 8k).
MAX_PIXELS = 64_000_000


class InvalidImageError(ValueError):
    """The bytes are not an accepted image, or are unsafe to decode."""


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    mime: str
    width: int
    height: int


def sniff_mime(data: bytes) -> str | None:
    """The image MIME type the bytes really are, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _open(data: bytes) -> Image.Image:
    if sniff_mime(data) is None:
        raise InvalidImageError("not a PNG, JPEG or WebP image")
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("the image could not be read") from exc

    if image.format not in ACCEPTED_FORMATS:
        raise InvalidImageError(f"unsupported image format {image.format}")
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise InvalidImageError("the image is too large to process")
    return image


def prepare(data: bytes, max_side: int = 1024) -> PreparedImage:
    """Validate *data* and return it scaled to fit *max_side*, re-encoded.

    Raises InvalidImageError for anything that is not a sane PNG/JPEG/WebP.
    """
    image = _open(data)
    try:
        image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("the image could not be decoded") from exc

    # JPEGs from phones carry their rotation in EXIF; apply it so captions
    # and the vision model see the picture the right way up.
    try:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
    except Exception:  # noqa: BLE001 - a bad EXIF block is not worth failing on
        pass

    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    photo = image.format == "JPEG" or (image.format == "WEBP" and not has_alpha)

    out = io.BytesIO()
    if photo:
        image.convert("RGB").save(out, format="JPEG", quality=88, optimize=True)
        mime = "image/jpeg"
    else:
        image.convert("RGBA" if has_alpha else "RGB").save(out, format="PNG", optimize=True)
        mime = "image/png"

    return PreparedImage(data=out.getvalue(), mime=mime, width=image.width, height=image.height)


def dimensions(data: bytes) -> tuple[int, int]:
    """Width and height without decoding the pixels."""
    return _open(data).size
