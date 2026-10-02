"""Turning a link into a source: what it is, what gets stored, what is refused.

Offline throughout: an httpx.MockTransport stands in for the web and a fake
resolver for DNS, so the address checks are exercised without a network.
"""

import json

import httpx
import pytest

from application.services.ingest import (
    UrlSourceService,
    google_sheet_reference,
    transcript_page,
    youtube_video_id,
)
from shared.enums import AssetType
from shared.exceptions import LinkSourceError

PUBLIC = "93.184.216.34"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://docs.google.com/spreadsheets/d/abc_123/edit#gid=42", ("abc_123", "42")),
        ("https://docs.google.com/spreadsheets/d/abc_123/edit?gid=7", ("abc_123", "7")),
        ("https://example.com/spreadsheets/d/abc_123/edit", None),
    ],
)
def test_google_sheet_urls_are_parsed(url, expected):
    assert google_sheet_reference(url) == expected

ARTICLE = (
    "<html><head><title>How rivers carve canyons</title></head><body>"
    "<nav>Home | News | Sport | Weather</nav>"
    "<article><h1>How rivers carve canyons</h1>"
    + "".join(
        f"<p>Paragraph {i}: over millions of years a river cuts down through "
        "rock layers, carrying sediment away and deepening its bed a little "
        "every flood season.</p>"
        for i in range(8)
    )
    + "</article><footer>Cookie settings | Privacy | Contact us</footer></body></html>"
)


def resolver(mapping: dict[str, str] | None = None):
    """DNS for the test: every host is public unless *mapping* says otherwise."""

    async def resolve(host, port):
        return [(mapping or {}).get(host, PUBLIC)]

    return resolve


def controller(handler, mapping=None) -> UrlSourceService:
    return UrlSourceService(transport=httpx.MockTransport(handler), resolver=resolver(mapping))


# --- recognising a YouTube link --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_every_youtube_link_shape_names_its_video(url):
    assert youtube_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/channel/UC123",
        "https://www.youtube.com/watch?v=short",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
        "https://example.com/article",
    ],
)
def test_other_links_are_not_videos(url):
    assert youtube_video_id(url) is None


# --- what the server will not fetch ----------------------------------------------


@pytest.mark.parametrize(
    "address",
    ["169.254.169.254", "10.0.0.5", "127.0.0.1", "192.168.1.10", "::1", "::ffff:10.0.0.1"],
    ids=["cloud metadata", "private", "loopback", "home network", "ipv6 loopback", "mapped private"],
)
async def test_an_internal_address_is_refused(address):
    """The server fetches what the user names; unchecked, that includes its
    own cloud credentials at 169.254.169.254."""
    fetched = []

    def handler(request):
        fetched.append(request.url)
        return httpx.Response(200, text="secret")

    with pytest.raises(LinkSourceError, match="not reachable"):
        await controller(handler, {"internal.test": address}).fetch("http://internal.test/")

    assert fetched == [], "the request was sent before the address was checked"


async def test_a_redirect_to_an_internal_address_is_refused():
    """Each hop is checked, not only the first: a public page redirecting to
    the metadata service is the classic way around a one-time check."""

    def handler(request):
        if request.url.host == "public.test":
            return httpx.Response(302, headers={"location": "http://internal.test/latest/meta-data"})
        return httpx.Response(200, text="credentials")

    with pytest.raises(LinkSourceError, match="not reachable"):
        await controller(handler, {"internal.test": "169.254.169.254"}).fetch("https://public.test/go")


@pytest.mark.parametrize("url", ["ftp://example.com/file.pdf", "file:///etc/passwd", "javascript:alert(1)", ""])
async def test_only_http_links_are_accepted(url):
    with pytest.raises(LinkSourceError):
        await controller(lambda request: httpx.Response(200)).fetch(url)


async def test_a_file_over_the_limit_is_refused(monkeypatch):
    c = controller(lambda request: httpx.Response(200, content=b"%PDF-" + b"x" * 5000))
    monkeypatch.setattr(c.settings, "MAX_FILE_SIZE", 1000)

    with pytest.raises(LinkSourceError, match="larger than"):
        await c.fetch("https://example.test/big.pdf")


async def test_too_many_redirects_give_up():
    def handler(request):
        return httpx.Response(302, headers={"location": f"https://example.test/{len(str(request.url))}"})

    with pytest.raises(LinkSourceError, match="too many"):
        await controller(handler).fetch("https://example.test/start")


async def test_an_error_status_is_reported():
    with pytest.raises(LinkSourceError, match="404"):
        await controller(lambda request: httpx.Response(404)).fetch("https://example.test/gone")


# --- PDF -------------------------------------------------------------------------


async def test_a_pdf_is_stored_as_the_file_itself():
    body = b"%PDF-1.7\n...the file..."
    fetched = await controller(
        lambda request: httpx.Response(200, content=body, headers={"content-type": "application/pdf"})
    ).fetch("https://example.test/papers/rivers.pdf")

    assert fetched.asset_type == AssetType.PDF
    assert fetched.content == body
    assert fetched.name == "rivers.pdf"
    # The bytes' hash, as for an upload: the file and its link are one document.
    assert fetched.content_hash is None


async def test_a_public_google_sheet_is_exported_as_csv():
    csv_body = b"Name,Department\nAda,Research\n"

    def handler(request):
        assert request.url.host == "docs.google.com"
        assert request.url.path == "/spreadsheets/d/abc_123/export"
        assert request.url.params["format"] == "csv"
        assert request.url.params["gid"] == "42"
        return httpx.Response(200, content=csv_body, headers={"content-type": "text/csv"})

    fetched = await controller(handler).fetch("https://docs.google.com/spreadsheets/d/abc_123/edit#gid=42")

    assert fetched.asset_type == AssetType.CSV
    assert fetched.content == csv_body
    assert fetched.content_type == "text/csv"
    assert fetched.source_url.endswith("#gid=42")
    assert fetched.content_hash


async def test_a_private_google_sheet_is_refused():
    with pytest.raises(LinkSourceError, match="not publicly accessible"):
        await controller(
            lambda request: httpx.Response(200, text="<html>Sign in</html>", headers={"content-type": "text/html"})
        ).fetch("https://docs.google.com/spreadsheets/d/private/edit")


async def test_a_pdf_is_recognised_by_its_bytes_whatever_the_server_says():
    fetched = await controller(
        lambda request: httpx.Response(
            200,
            content=b"%PDF-1.4 body",
            headers={"content-type": "application/octet-stream"},
        )
    ).fetch("https://example.test/download?id=7")

    assert fetched.asset_type == AssetType.PDF
    assert fetched.name.endswith(".pdf")


async def test_a_pdf_is_named_from_content_disposition():
    fetched = await controller(
        lambda request: httpx.Response(
            200,
            content=b"%PDF-1.4",
            headers={"content-disposition": "attachment; filename*=UTF-8''%D8%AF%D9%84%D9%8A%D9%84.pdf"},
        )
    ).fetch("https://example.test/download?id=7")

    assert fetched.name == "دليل.pdf"


# --- article ---------------------------------------------------------------------


async def test_an_article_is_stored_as_markdown_without_the_page_around_it():
    fetched = await controller(
        lambda request: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html; charset=utf-8"})
    ).fetch("https://news.test/rivers#comments")

    text = fetched.content.decode()

    assert fetched.asset_type == AssetType.MARKDOWN
    assert fetched.name == "How rivers carve canyons.md"
    assert fetched.source_url == "https://news.test/rivers"
    assert "carrying sediment away" in text
    assert "Cookie settings" not in text and "Home | News" not in text


async def test_the_same_article_is_one_source_whatever_its_text_does():
    """Identity is the link: a page's text shifts with every ad block."""
    one = await controller(lambda request: httpx.Response(200, text=ARTICLE)).fetch("https://news.test/rivers")
    two = await controller(
        lambda request: httpx.Response(200, text=ARTICLE.replace("Paragraph 0", "Paragraph zero"))
    ).fetch("https://news.test/rivers#top")

    assert one.content_hash == two.content_hash


async def test_a_page_with_no_article_says_so():
    """A JavaScript shell: the text only arrives once a browser runs it."""
    shell = "<html><body><div id='root'></div><script src='app.js'></script></body></html>"

    with pytest.raises(LinkSourceError, match="JavaScript"):
        await controller(lambda request: httpx.Response(200, text=shell, headers={"content-type": "text/html"})).fetch(
            "https://app.test/"
        )


async def test_an_unsupported_type_is_refused():
    with pytest.raises(LinkSourceError, match="not a PDF, an article"):
        await controller(
            lambda request: httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"})
        ).fetch("https://example.test/cat.png")


# --- YouTube ---------------------------------------------------------------------

SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "welcome to the talk"},
    {"start": 4.0, "end": 9.5, "text": "today we look at rivers"},
    {"start": 9.5, "end": 15.0, "text": "and how they carve canyons"},
]


async def test_a_video_is_stored_as_its_transcript(monkeypatch):
    def handler(request):
        assert request.url.path == "/oembed"
        return httpx.Response(200, json={"title": "Rivers, explained"})

    c = controller(handler)
    monkeypatch.setattr(c, "_transcript", lambda video_id: ("en", False, SEGMENTS))

    fetched = await c.fetch("https://youtu.be/dQw4w9WgXcQ")
    payload = json.loads(fetched.content)

    assert fetched.asset_type == AssetType.YOUTUBE
    assert fetched.name == "Rivers, explained"
    assert fetched.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert payload["video_id"] == "dQw4w9WgXcQ"
    assert payload["segments"] == SEGMENTS


async def test_the_same_video_is_one_source(monkeypatch):
    c = controller(lambda request: httpx.Response(200, json={"title": "t"}))
    monkeypatch.setattr(c, "_transcript", lambda video_id: ("en", False, SEGMENTS))

    one = await c.fetch("https://youtu.be/dQw4w9WgXcQ")
    two = await c.fetch("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s")

    assert one.content_hash == two.content_hash


async def test_a_missing_title_falls_back_to_the_video_id(monkeypatch):
    c = controller(lambda request: httpx.Response(404))
    monkeypatch.setattr(c, "_transcript", lambda video_id: ("en", False, SEGMENTS))

    assert (await c.fetch("https://youtu.be/dQw4w9WgXcQ")).name == "YouTube video dQw4w9WgXcQ"


def _fake_api(monkeypatch, raises):
    import youtube_transcript_api

    class Api:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            raise raises

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", Api)


def test_a_video_without_captions_says_so(monkeypatch):
    from youtube_transcript_api import TranscriptsDisabled

    _fake_api(monkeypatch, TranscriptsDisabled("dQw4w9WgXcQ"))

    with pytest.raises(LinkSourceError, match="no transcript"):
        UrlSourceService()._transcript("dQw4w9WgXcQ")


def test_a_blocked_server_is_told_about_the_proxy(monkeypatch):
    from youtube_transcript_api import IpBlocked

    _fake_api(monkeypatch, IpBlocked("dQw4w9WgXcQ"))

    with pytest.raises(LinkSourceError, match="YOUTUBE_PROXY_URL"):
        UrlSourceService()._transcript("dQw4w9WgXcQ")


def test_a_manual_transcript_is_preferred_to_a_generated_one(monkeypatch):
    import youtube_transcript_api
    from youtube_transcript_api import NoTranscriptFound

    class Snippet:
        def __init__(self, text, start, duration):
            self.text, self.start, self.duration = text, start, duration

    class Transcript:
        def __init__(self, code, generated):
            self.language_code, self.is_generated = code, generated

        def fetch(self):
            return [Snippet("  hello\nthere ", 1.0, 2.5), Snippet("", 3.5, 1.0)]

    class Listing:
        def find_manually_created_transcript(self, languages):
            if "en" in languages:
                return Transcript("en", False)
            raise NoTranscriptFound("v", languages, None)

        def find_generated_transcript(self, languages):
            return Transcript("en", True)

        def __iter__(self):
            return iter([Transcript("en", True)])

    class Api:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            return Listing()

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", Api)

    language, generated, segments = UrlSourceService()._transcript("dQw4w9WgXcQ")

    assert (language, generated) == ("en", False)
    # Whitespace collapsed; an empty caption dropped rather than stored as a blank line.
    assert segments == [{"start": 1.0, "end": 3.5, "text": "hello there"}]


# --- from transcript to timestamped chunks ---------------------------------------


def test_a_transcript_page_maps_every_line_to_its_moment():
    page = transcript_page(json.dumps({"segments": SEGMENTS}).encode())

    assert page["text"].splitlines() == [s["text"] for s in SEGMENTS]
    assert page["skip_correction"] is True

    for (offset, start, end), segment in zip(page["timeline"], SEGMENTS):
        assert page["text"][offset:].startswith(segment["text"])
        assert (start, end) == (segment["start"], segment["end"])


def test_every_chunk_gets_the_stretch_of_video_it_quotes():
    """Through the real splitter: each chunk's range starts at the line holding
    its first character and ends with the line holding its last."""
    from application.services import ProcessService
    from application.tasks.jobs.ingest.assemble import _documents

    segments = [
        {"start": float(i * 5), "end": float(i * 5 + 5), "text": f"sentence number {i} about the river delta"}
        for i in range(60)
    ]
    page = transcript_page(json.dumps({"segments": segments}).encode())

    documents, layout, scales, timelines = _documents("talk", [page])
    controller = ProcessService(chunk_size=300, chunk_overlap=0)
    controller._pdf_pages, controller._text_scale, controller._timelines = layout, scales, timelines
    controller.text.sanitize(documents)

    chunks = controller.split_file(documents)

    assert len(chunks) > 3
    for chunk in chunks:
        start, end = chunk.metadata["time_range"]
        first = int(chunk.page_content.split("sentence number ")[1].split()[0])
        last = int(chunk.page_content.split("sentence number ")[-1].split()[0])

        assert start == first * 5.0, chunk.page_content[:60]
        assert end == last * 5.0 + 5.0, chunk.page_content[-60:]


def test_a_citation_of_a_video_names_the_moment():
    from application.services.rag.citations import located_from_metadata

    assert located_from_metadata({"page": 0, "time_range": [754.2, 790.0]}) == {
        "page_number": None,
        "page_label": None,
        "time_start": 754.2,
        "time_label": "12:34",
    }
    assert located_from_metadata({"time_range": [3725.0, 3800.0]})["time_label"] == "1:02:05"


# --- the ingest stages -----------------------------------------------------------


def test_a_video_is_never_split_into_page_batches():
    """Named after its video, and a title can end in ".pdf"."""
    from types import SimpleNamespace

    from application.tasks.jobs.ingest.process import _batches_for
    from shared.utils import get_settings

    asset = SimpleNamespace(asset_type=AssetType.YOUTUBE, name="Reading a paper.pdf")

    assert _batches_for(asset, get_settings()) == [(None, None)]


async def test_link_sources_skip_the_repair_pass(monkeypatch):
    from application.tasks.jobs.ingest import postprocess
    from shared.utils import get_settings

    called = []
    monkeypatch.setattr(postprocess, "ProviderCache", lambda settings: called.append(1))
    monkeypatch.setattr(get_settings(), "POSTPROCESS_ENABLED", True)

    pages = [{"page_index": 0, "text": "a transcript", "skip_correction": True}]

    assert await postprocess._correct(pages, get_settings()) == 0
    assert called == [], "a model client was built for pages that skip correction"


def test_the_spoken_language_beats_a_preferred_translation(monkeypatch):
    """An English talk with Arabic subtitles: the Arabic track is a
    translation, and a citation should quote what is actually said."""
    import youtube_transcript_api
    from youtube_transcript_api import NoTranscriptFound

    class Transcript:
        def __init__(self, code, generated):
            self.language_code, self.is_generated = code, generated

        def fetch(self):
            return [type("S", (), {"text": self.language_code, "start": 0.0, "duration": 1.0})()]

    manual = {"ar": Transcript("ar", False), "en": Transcript("en", False)}

    class Listing:
        def find_manually_created_transcript(self, languages):
            for code in languages:
                if code in manual:
                    return manual[code]
            raise NoTranscriptFound("v", languages, None)

        def find_generated_transcript(self, languages):
            raise NoTranscriptFound("v", languages, None)

        def __iter__(self):
            return iter([*manual.values(), Transcript("en", True)])

    class Api:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            return Listing()

    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", Api)
    c = UrlSourceService()
    monkeypatch.setattr(c.settings, "YOUTUBE_TRANSCRIPT_LANGUAGES", ["ar", "en"])

    language, generated, _ = c._transcript("dQw4w9WgXcQ")

    assert (language, generated) == ("en", False)
