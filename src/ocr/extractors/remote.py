"""Engines that run somewhere else, reached over HTTP.

The deployment target is a 2-vCPU box with no GPU. Qari reads Arabic at roughly
a third the word error rate of anything that will run there, and needs 5 GB of
VRAM to do it. Those two facts do not resolve locally, so the model runs on a
Colab GPU and the server talks to it through an ngrok tunnel — see
`ocr/colab/qari_server.ipynb` for the other end.

That makes this the only extractor here whose failures are mostly *not* about
Arabic: the tunnel dies when the Colab session expires, its hostname changes on
every restart, and the free tier serves an HTML interstitial to anything it
mistakes for a browser. Each of those is handled explicitly below, because each
one otherwise surfaces as "the OCR returned nothing".
"""

from __future__ import annotations

import os

from ..base import ArabicExtractor, Page


class QariRemoteExtractor(ArabicExtractor):
    """Qari-OCR over HTTP, against the Colab notebook in `ocr/colab/`.

    Sends one page at a time as a one-page PDF rather than posting the whole
    document and asking for a page range: the corpus that motivated this
    contains a 25 MB book, and uploading it once per page over a home
    connection would cost more than the inference does.
    """

    name = "qari-remote"
    description = "Qari-OCR on a remote GPU (Colab + ngrok), over HTTP"

    #: ~30 s/page measured on a real book page, and a T4 is slower than the
    #: card that was measured on. A minute per page leaves room for the tunnel.
    DEFAULT_TIMEOUT = 120

    @staticmethod
    def _config() -> tuple[str, str, int]:
        """URL, secret and timeout, from the environment or the app's settings.

        Environment first, so this package still runs standalone, and because
        the URL is not a constant: a free ngrok tunnel gets a new hostname
        every time the notebook restarts. Anything that hard-codes it will be
        wrong by tomorrow.
        """
        url = os.environ.get("QARI_REMOTE_URL", "")
        secret = os.environ.get("QARI_REMOTE_SECRET", "")
        timeout = os.environ.get("QARI_REMOTE_TIMEOUT", "")

        if not url:
            try:
                from utils import get_settings

                settings = get_settings()
                url = getattr(settings, "QARI_REMOTE_URL", "") or ""
                secret = getattr(settings, "QARI_REMOTE_SECRET", "") or ""
                timeout = str(getattr(settings, "QARI_REMOTE_TIMEOUT", "") or "")
            except Exception:  # noqa: BLE001 - standalone use is expected
                pass

        return (
            url.rstrip("/"),
            secret,
            int(timeout) if timeout.isdigit() else QariRemoteExtractor.DEFAULT_TIMEOUT,
        )

    @classmethod
    def available(cls) -> tuple[bool, str]:
        url, secret, _ = cls._config()

        if not url:
            return False, "QARI_REMOTE_URL is not set (start ocr/colab/qari_server.ipynb)"

        if not secret:
            return False, "QARI_REMOTE_SECRET is not set; the endpoint requires a bearer token"

        # Reachability is checked, not assumed. The tunnel outlives neither the
        # Colab session nor the day, so "configured" and "answering" are
        # different questions and only the second one matters.
        try:
            import json
            import urllib.request

            request = urllib.request.Request(f"{url}/health", headers=cls._headers(secret))
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.load(response)
        except Exception as exc:  # noqa: BLE001
            return False, f"{url} did not answer /health: {type(exc).__name__}: {exc}"

        if body.get("status") != "ok":
            return False, f"{url} answered /health with {body!r}"

        return True, ""

    @staticmethod
    def _headers(secret: str) -> dict:
        return {
            "Authorization": f"Bearer {secret}",
            # Without this, ngrok's free tier serves a browser interstitial
            # instead of proxying, and the JSON parse fails on an HTML page —
            # which reads as "the model returned nothing" rather than "the
            # request never reached it".
            "ngrok-skip-browser-warning": "true",
            "User-Agent": "notebookllm-minus/ocr",
        }

    @staticmethod
    def _one_page_pdf(page: Page) -> bytes:
        """Just this page, as its own PDF, to keep the upload small."""
        import pymupdf

        source = pymupdf.open(page.path)

        try:
            single = pymupdf.open()
            single.insert_pdf(source, from_page=page.number, to_page=page.number)
            data = single.tobytes()
            single.close()
            return data
        finally:
            source.close()

    def _extract(self, page: Page) -> str:
        import json
        import urllib.error
        import urllib.request
        import uuid

        url, secret, timeout = self._config()

        pdf = self._one_page_pdf(page)
        boundary = uuid.uuid4().hex

        # Built by hand rather than pulling in requests: the application image
        # does not carry it, and a multipart body with three fields is not
        # worth a dependency.
        parts = []
        for field, value in (("first_page", "0"), ("last_page", "0")):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"' f"\r\n\r\n{value}\r\n".encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="page-{page.number}.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n".encode()
        )
        parts.append(pdf)
        parts.append(f"\r\n--{boundary}--\r\n".encode())

        headers = self._headers(secret)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

        request = urllib.request.Request(f"{url}/ocr", data=b"".join(parts), headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            raise RuntimeError(f"remote OCR returned HTTP {exc.code}: {detail}") from exc
        except json.JSONDecodeError as exc:
            # Almost always the ngrok interstitial or an expired tunnel serving
            # its own error page.
            raise RuntimeError(
                f"{url} returned something that is not JSON — the tunnel is " "probably down or pointing somewhere else"
            ) from exc

        pages = body.get("pages") or []

        if not pages:
            raise RuntimeError(f"remote OCR returned no pages for {page.number}")

        return pages[0].get("text", "")
