"""Turning a link into a source: an online PDF, an article, or a YouTube video.

Everything after "here are the bytes" is the upload's path -- `store` and
`queue` in AssetIngestService -- so this module only answers what the link
is and what to store for it:

    PDF       the file itself, exactly as an upload would store it
    article   the page's main text as markdown, via trafilatura, so it takes
              the markdown path: preview, chunking and text_range highlights
    YouTube   the transcript as JSON with a start and end per line, which the
              ingest turns into a time range on every chunk

It runs inside the request, not on a worker, because the useful failures are
the user's to act on -- a video with no captions, a page that only renders with
JavaScript, a dead link -- and they should hear it when they paste the link,
not as a failed row a minute later.

**The server fetches what the user names.** Unchecked, that is a request to
`http://169.254.169.254/` returning this machine's cloud credentials, or to
the database on the private network. So every hop -- the first URL and each
redirect -- is resolved and refused unless every address it resolves to is a
public one, redirects are followed here rather than by httpx so each one is
checked, and the body is read in pieces that stop at MAX_FILE_SIZE. What this
does not close is DNS rebinding between the check and httpx's own resolution;
that needs pinning the checked address into the connection, which is a larger
change than this feature.
"""

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import httpx

from shared.enums import AssetType
from shared.exceptions import LinkSourceError

from ..core.BaseService import BaseService
from .TextProcessingService import normalize_text, strip_nulls

#: Redirects followed before giving up. Enough for http->https->www->canonical.
MAX_REDIRECTS = 5

#: An article shorter than this is almost certainly not one: a cookie wall, a
#: login page, or a JavaScript shell whose text never arrived.
MIN_ARTICLE_CHARS = 200

#: Some sites answer a library's default User-Agent with 403.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class FetchedSource:
    """What `AssetIngestService.store` needs, for a source from a link."""

    name: str
    content: bytes
    content_type: str
    asset_type: AssetType
    source_url: str
    #: None means "hash the bytes", as for an upload.
    content_hash: str | None = None


def youtube_video_id(url: str) -> str | None:
    """The video id a YouTube link names, or None if it is not one."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]

    candidate = None

    if host == "youtu.be" and path:
        candidate = path[0]
    elif host in _YOUTUBE_HOSTS:
        if parts.path == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [None])[0]
        elif len(path) >= 2 and path[0] in ("shorts", "live", "embed", "v"):
            candidate = path[1]

    return candidate if candidate and _VIDEO_ID.match(candidate) else None


def transcript_page(file_bytes: bytes) -> dict:
    """A stored YouTube transcript as one page, with its timeline.

    The text is one transcript line per text line, which is also how the
    preview lists them. ``timeline`` holds ``[char_offset, start, end]`` for
    each line, so a chunk's character range can be turned back into the
    stretch of video it came from -- see `ProcessService.split_file`.
    """
    payload = json.loads(file_bytes.decode("utf-8"))

    lines: list[str] = []
    timeline: list[list[float]] = []
    offset = 0

    for segment in payload.get("segments", []):
        # Normalised here, not left to `sanitize`: NFKC can change a line's
        # length, and the offsets below have to index the text as it will be
        # split. normalize_text is idempotent, so sanitize then changes nothing.
        text = normalize_text(strip_nulls(segment["text"]))
        timeline.append([offset, float(segment["start"]), float(segment["end"])])
        lines.append(text)
        offset += len(text) + 1  # the newline joining it to the next

    return {
        "page_index": 0,
        "page_label": "1",
        "width": 0.0,
        "height": 0.0,
        "text": "\n".join(lines),
        "starts": [],
        "words": [],
        "boxes": [],
        "timeline": timeline,
        # Captions are what was said, not a broken text layer: nothing for the
        # repair pass to fix, and one whole-video "page" would only be rejected
        # by its length guard after costing a long call.
        "skip_correction": True,
    }


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)

    # An IPv4 address carried in IPv6 is judged as the IPv4 address it is.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    return ip.is_global and not ip.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class UrlSourceService(BaseService):
    """Fetch a link and say what to store for it.

    *transport* and *resolver* exist for tests: an ``httpx.MockTransport``
    and a fake DNS, so nothing here needs the network to be exercised.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, resolver=None) -> None:
        super().__init__()
        self._transport = transport
        self._resolver = resolver or _resolve

    async def fetch(self, url: str) -> FetchedSource:
        url = self._validate(url)

        video_id = youtube_video_id(url)

        try:
            async with asyncio.timeout(self.settings.URL_FETCH_TIMEOUT):
                if video_id:
                    return await self._youtube(video_id)

                final_url, content_type, headers, body = await self._download(url)
        except TimeoutError as exc:
            raise LinkSourceError(f"The link took longer than {self.settings.URL_FETCH_TIMEOUT}s to answer.") from exc

        if "application/pdf" in content_type or body[:5] == b"%PDF-":
            return self._pdf(final_url, headers, body)

        if "html" in content_type or body.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
            return await self._article(final_url, body)

        raise LinkSourceError(
            "This link is not a PDF, an article or a YouTube video "
            f"(it returned {content_type or 'an unknown type'})."
        )

    # --- checking the link -----------------------------------------------------

    @staticmethod
    def _validate(url: str) -> str:
        url = (url or "").strip()

        if not url:
            raise LinkSourceError("Paste a link to add.")

        if len(url) > 2048:
            raise LinkSourceError("That link is too long.")

        parts = urlsplit(url)

        if parts.scheme not in ("http", "https"):
            raise LinkSourceError("Only http and https links can be added.")

        if not parts.hostname:
            raise LinkSourceError("That does not look like a link.")

        return url

    async def _check_host(self, url: str) -> None:
        """Refuse a URL unless every address its host resolves to is public."""
        parts = urlsplit(url)

        if parts.scheme not in ("http", "https"):
            raise LinkSourceError("Only http and https links can be added.")

        port = parts.port or (443 if parts.scheme == "https" else 80)

        try:
            addresses = await self._resolver(parts.hostname, port)
        except (OSError, UnicodeError) as exc:
            raise LinkSourceError(f"Could not find {parts.hostname!r}.") from exc

        # Every address, not the first: a host resolving to one public and one
        # private address would otherwise pass here and connect to either.
        if not addresses or not all(_is_public(address) for address in addresses):
            self.logger.warning("Refused a link to %s (%s)", parts.hostname, ", ".join(addresses))
            raise LinkSourceError("That address is not reachable from here.")

    # --- downloading -----------------------------------------------------------

    async def _download(self, url: str) -> tuple[str, str, httpx.Headers, bytes]:
        """GET *url*, following redirects by hand. Returns the final URL too."""
        limit = self.settings.MAX_FILE_SIZE

        async with httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            headers=_HEADERS,
            timeout=httpx.Timeout(self.settings.URL_FETCH_TIMEOUT, connect=10),
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                await self._check_host(url)

                try:
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise LinkSourceError("The link redirected nowhere.")
                            url = urljoin(url, location)
                            continue

                        if response.status_code >= 400:
                            raise LinkSourceError(f"The link answered {response.status_code}.")

                        declared = int(response.headers.get("content-length") or 0)
                        if declared > limit:
                            raise LinkSourceError(self._too_large(limit))

                        body = bytearray()
                        async for piece in response.aiter_bytes():
                            body.extend(piece)
                            if len(body) > limit:
                                raise LinkSourceError(self._too_large(limit))

                        content_type = response.headers.get("content-type", "").lower()
                        return url, content_type, response.headers, bytes(body)

                except httpx.HTTPError as exc:
                    raise LinkSourceError(f"Could not fetch the link ({type(exc).__name__}).") from exc

        raise LinkSourceError("The link redirected too many times.")

    @staticmethod
    def _too_large(limit: int) -> str:
        return f"The file is larger than the {limit // (1024 * 1024)} MB limit."

    # --- PDF -------------------------------------------------------------------

    def _pdf(self, url: str, headers: httpx.Headers, body: bytes) -> FetchedSource:
        return FetchedSource(
            name=self._pdf_name(url, headers),
            content=body,
            content_type="application/pdf",
            asset_type=AssetType.PDF,
            source_url=url,
            # The bytes, as for an upload: the same PDF added by file and by
            # link is the same document.
            content_hash=None,
        )

    @staticmethod
    def _pdf_name(url: str, headers: httpx.Headers) -> str:
        disposition = headers.get("content-disposition", "")

        name = ""
        encoded = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", disposition, re.I)
        plain = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)

        if encoded:
            name = unquote(encoded.group(1).strip())
        elif plain:
            name = plain.group(1).strip()
        else:
            name = unquote(PurePosixPath(urlsplit(url).path).name)

        name = PurePosixPath(name.replace("\\", "/")).name.strip() or "document"

        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"

        return name[-200:]

    # --- article ---------------------------------------------------------------

    async def _article(self, url: str, body: bytes) -> FetchedSource:
        # CPU work on a possibly large page; off the event loop.
        title, text = await asyncio.to_thread(self._extract_article, body, url)

        if not text or len(text) < MIN_ARTICLE_CHARS:
            raise LinkSourceError(
                "Could not find article text on this page. It may need "
                "JavaScript to show its content, or it may not be an article."
            )

        title = title or urlsplit(url).hostname or "Article"
        markdown = text if text.lstrip().startswith("#") else f"# {title}\n\n{text}"

        canonical = urlunsplit(urlsplit(url)._replace(fragment=""))

        return FetchedSource(
            name=f"{self._safe_name(title)[:196]}.md",
            content=markdown.encode("utf-8"),
            content_type="text/markdown",
            asset_type=AssetType.MARKDOWN,
            source_url=canonical,
            # The link, not the text: a page's extracted text shifts with every
            # ad and "related stories" block, and the same article twice is
            # still a duplicate.
            content_hash=hashlib.sha256(f"url:{canonical}".encode()).hexdigest(),
        )

    @staticmethod
    def _extract_article(body: bytes, url: str) -> tuple[str, str]:
        import trafilatura

        text = trafilatura.extract(
            body,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        metadata = trafilatura.extract_metadata(body, default_url=url)
        title = (getattr(metadata, "title", None) or "").strip()

        return title, (text or "").strip()

    @staticmethod
    def _safe_name(title: str) -> str:
        name = re.sub(r"[\\/\x00-\x1f]+", " ", title)
        return re.sub(r"\s+", " ", name).strip() or "Article"

    # --- YouTube ---------------------------------------------------------------

    async def _youtube(self, video_id: str) -> FetchedSource:
        # youtube-transcript-api is synchronous (requests); off the event loop.
        language, generated, segments = await asyncio.to_thread(self._transcript, video_id)
        title = await self._video_title(video_id)
        watch_url = f"https://www.youtube.com/watch?v={video_id}"

        payload = {
            "video_id": video_id,
            "title": title,
            "language": language,
            "is_generated": generated,
            "segments": segments,
        }

        return FetchedSource(
            name=self._safe_name(title)[:200],
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            asset_type=AssetType.YOUTUBE,
            source_url=watch_url,
            content_hash=hashlib.sha256(f"youtube:{video_id}".encode()).hexdigest(),
        )

    def _transcript(self, video_id: str) -> tuple[str, bool, list[dict]]:
        """The best transcript of what is actually said in the video.

        In order: a manual transcript in the language spoken, then a manual one
        in a preferred language, then a generated one, then anything. The
        spoken language comes first because a preferred-language manual track
        is often a *translation* -- an English TED talk's Arabic subtitles --
        and a notebook citing a sentence nobody in the video says is wrong
        however well it reads. YouTube's auto-generated track is in the spoken
        language, which is how that language is known.
        """
        from youtube_transcript_api import (
            IpBlocked,
            NoTranscriptFound,
            RequestBlocked,
            TranscriptsDisabled,
            VideoUnavailable,
            YouTubeTranscriptApi,
        )
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy = self.settings.YOUTUBE_PROXY_URL
        api = YouTubeTranscriptApi(proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy) if proxy else None)
        languages = list(self.settings.YOUTUBE_TRANSCRIPT_LANGUAGES) or ["en"]

        try:
            available = api.list(video_id)

            spoken = next((t.language_code for t in available if t.is_generated), None)
            attempts = [
                (available.find_manually_created_transcript, [spoken] if spoken else []),
                (available.find_manually_created_transcript, languages),
                (available.find_generated_transcript, [spoken] if spoken else []),
                (available.find_generated_transcript, languages),
            ]

            transcript = None
            for find, codes in attempts:
                if not codes:
                    continue
                try:
                    transcript = find(codes)
                    break
                except NoTranscriptFound:
                    continue

            if transcript is None:
                transcript = next(iter(available), None)

            if transcript is None:
                raise LinkSourceError("This video has no transcript available.")

            fetched = transcript.fetch()

        except (TranscriptsDisabled, NoTranscriptFound) as exc:
            raise LinkSourceError("This video has no transcript available.") from exc
        except VideoUnavailable as exc:
            raise LinkSourceError("This video is unavailable.") from exc
        except (RequestBlocked, IpBlocked) as exc:
            raise LinkSourceError(
                "YouTube refused the transcript request from this server. "
                "Setting YOUTUBE_PROXY_URL is the usual fix."
            ) from exc
        except LinkSourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - the library raises many kinds
            raise LinkSourceError(f"Could not read this video's transcript ({type(exc).__name__}).") from exc

        segments = []
        for snippet in fetched:
            text = " ".join(snippet.text.split())
            if text:
                start = round(float(snippet.start), 2)
                segments.append({"start": start, "end": round(start + float(snippet.duration), 2), "text": text})

        if not segments:
            raise LinkSourceError("This video's transcript is empty.")

        return transcript.language_code, bool(transcript.is_generated), segments

    async def _video_title(self, video_id: str) -> str:
        """The title from YouTube's oEmbed endpoint, which needs no API key."""
        fallback = f"YouTube video {video_id}"

        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10, headers=_HEADERS) as client:
                response = await client.get(
                    "https://www.youtube.com/oembed",
                    params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
                )
            if response.status_code != 200:
                return fallback
            return (response.json().get("title") or "").strip() or fallback
        except (httpx.HTTPError, ValueError):
            return fallback
