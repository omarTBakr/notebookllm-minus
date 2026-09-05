"""Vision models someone else runs, reached over an API.

Split from `vision_models.py` because the operational shape is different, not
the task. A local model is bounded by hardware you control: it either fits in
VRAM or it does not, and once loaded it costs nothing per page. A hosted one
has no VRAM cost, no model download and no cold start -- and in exchange every
page is a network round trip, the rate limit belongs to somebody else, and the
document leaves the machine.

That last point decides where these can be used. They are benchmark candidates
and a deliberate path for a document worth re-reading properly; they are not
what bulk ingestion runs.
"""

from __future__ import annotations

from ..base import ArabicExtractor, Page
from .vision_models import TRANSCRIBE_PROMPT


class GeminiExtractor(ArabicExtractor):
    """Google's multimodal model, over the API.

    Included because it was already measured exact on a controlled Arabic
    sample in this project — 4/4 lines, character for character — which makes
    it the closest thing to a reference reading available without transcribing
    pages by hand. It is metered and rate-limited, so it is the yardstick here
    rather than a candidate for bulk ingestion.
    """

    name = "gemini"
    description = "Gemini multimodal via the API (metered)"

    @staticmethod
    def _credentials() -> tuple[str, str]:
        """The API key and model, from the environment or the app's settings.

        Environment first so this package can be run on its own — the whole
        point of keeping its imports lazy — and the application's Settings as a
        fallback when it happens to be importable.
        """
        import os

        key = os.environ.get("GOOGLE_API_KEY", "")
        model = os.environ.get("GOOGLE_MODEL_ID", "gemini-3.6-flash")

        if not key:
            try:
                from utils import get_settings

                settings = get_settings()
                key = settings.GOOGLE_API_KEY or ""
                model = settings.GOOGLE_MODEL_ID
            except Exception:  # noqa: BLE001 - standalone use is expected
                pass

        return key, model

    @classmethod
    def available(cls) -> tuple[bool, str]:
        key, _ = cls._credentials()

        if not key:
            return False, "GOOGLE_API_KEY is not set in the environment or .env"

        return True, ""

    def _extract(self, page: Page) -> str:
        import base64
        import json
        import urllib.request

        key, default_model = self._credentials()
        model = self.options.get("model", default_model)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": TRANSCRIBE_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": base64.b64encode(page.png_bytes).decode(),
                            }
                        },
                    ]
                }
            ],
            # Generous, because a dense page is a lot of tokens and a thinking
            # model spends some of the budget before it writes anything.
            "generationConfig": {
                "maxOutputTokens": self.options.get("max_output_tokens", 8192),
                "temperature": 0,
            },
        }

        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/" f"{model}:generateContent?key={key}",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.load(response)

        candidates = body.get("candidates") or [{}]
        parts = candidates[0].get("content", {}).get("parts", [])

        return "".join(part.get("text", "") for part in parts)


class OpenRouterExtractor(ArabicExtractor):
    """Any vision model on OpenRouter, over its OpenAI-compatible API.

    One class rather than one per vendor: OpenRouter fronts dozens of
    multimodal models behind a single wire format, so the model id is
    configuration and this code does not change when the choice does. Point
    OPENROUTER_MODEL at whichever reads Arabic best on the day.

    The default is a free tier, which is the reason this is interesting at all.
    Qari reads Arabic better than anything that runs on CPU and needs 5 GB of
    VRAM and ~30 s a page on a T4 -- so slow that shipping pages to a GPU costs
    more than reading them locally with Tesseract. A hosted model moves the
    compute somewhere it already exists, and a free tier removes the argument
    against trying it.

    What it does not remove: the pages still leave the machine, the rate limit
    is someone else's to change, and a free endpoint can be withdrawn. This is
    a candidate to be measured against `tesseract-best`, not a default.
    """

    name = "openrouter"
    description = "A vision model on OpenRouter (default: minimax-m3 free tier)"

    #: Vision-capable and free at the time of writing. Verified against
    #: openrouter.ai/api/v1/models: input_modalities ['text', 'image', 'video'],
    #: 1M context. A text-only model here fails at request time with a message
    #: about image content, which is why `available()` checks the catalogue.
    DEFAULT_MODEL = "minimax/minimax-m3:free"

    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
    CATALOGUE = "https://openrouter.ai/api/v1/models"

    @staticmethod
    def _credentials() -> tuple[str, str]:
        """Key and model, environment first then the app's settings.

        Environment first so this package still runs standalone, which is the
        whole point of keeping its imports lazy.
        """
        import os

        key = os.environ.get("OPENROUTER_API_KEY", "")
        model = os.environ.get("OPENROUTER_MODEL", "")

        if not key:
            try:
                from utils import get_settings

                settings = get_settings()
                key = getattr(settings, "OPENROUTER_API_KEY", "") or ""
                model = model or (getattr(settings, "OPENROUTER_MODEL", "") or "")
            except Exception:  # noqa: BLE001 - standalone use is expected
                pass

        return key, model or OpenRouterExtractor.DEFAULT_MODEL

    @classmethod
    def available(cls) -> tuple[bool, str]:
        key, model = cls._credentials()

        if not key:
            return False, "OPENROUTER_API_KEY is not set in the environment or .env"

        # A text-only model does not refuse an image politely -- it either
        # ignores it and hallucinates a page, or fails with a message about
        # content parts. Both are worse than being told here, and the
        # catalogue is public and cheap to read.
        try:
            import json
            import urllib.request

            with urllib.request.urlopen(cls.CATALOGUE, timeout=15) as response:
                catalogue = json.load(response)

            entry = next((m for m in catalogue.get("data", []) if m.get("id") == model), None)
        except Exception as exc:  # noqa: BLE001 - offline is not a hard failure
            return True, f"(could not verify {model} against the catalogue: {exc})"

        if entry is None:
            return False, f"{model!r} is not on OpenRouter"

        modalities = entry.get("architecture", {}).get("input_modalities") or []

        if "image" not in modalities:
            return False, f"{model!r} does not accept images (input_modalities={modalities})"

        return True, ""

    def _extract(self, page: Page) -> str:
        import base64
        import json
        import urllib.error
        import urllib.request

        key, model = self._credentials()
        model = self.options.get("model", model)

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TRANSCRIBE_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + base64.b64encode(page.png_bytes).decode()},
                        },
                    ],
                }
            ],
            # 0 because this is transcription, not writing: any sampling at all
            # invents plausible Arabic where the page is unclear, which scores
            # worse than an honest gap and reads as if the model succeeded.
            "temperature": 0,
            "max_tokens": self.options.get("max_output_tokens", 8192),
        }

        request = urllib.request.Request(
            self.ENDPOINT,
            json.dumps(payload).encode(),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                # OpenRouter attributes requests by these; without them a free
                # tier is rate-limited harder.
                "HTTP-Referer": "https://github.com/omarTBakr/notebookllm-minus",
                "X-Title": "notebookllm-minus OCR benchmark",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            # 429 on a free tier is the expected failure, not an exception:
            # say so plainly rather than making it look like a model problem.
            raise RuntimeError(f"OpenRouter returned HTTP {exc.code}: {detail}") from exc

        if body.get("error"):
            raise RuntimeError(f"OpenRouter: {body['error']}")

        choices = body.get("choices") or []

        if not choices:
            raise RuntimeError(f"OpenRouter returned no choices: {str(body)[:200]}")

        text = choices[0].get("message", {}).get("content") or ""

        # Some models wrap a transcription in a fence despite being told not
        # to. Charging that to their error rate measures instruction-following
        # rather than reading -- the same mistake that cost Qari a factor of
        # four before its markup was stripped.
        return _strip_fences(text)


def _strip_fences(text: str) -> str:
    """Drop a leading/trailing markdown fence, keeping the contents."""
    import re

    stripped = text.strip()
    match = re.match(r"^```[a-zA-Z]*\n(.*?)\n?```$", stripped, re.DOTALL)

    return match.group(1).strip() if match else stripped
