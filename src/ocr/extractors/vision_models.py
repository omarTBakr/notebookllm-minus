"""Vision-language models: read the page the way a person would.

The class that KITAB-Bench (ACL 2025) found beats classical OCR on Arabic by
roughly 60% CER. The reason is structural rather than incidental. A detector
plus recogniser has to cut a cursive word into characters that were never
separate, and it reads each box with no idea what the sentence is about. A VLM
reads the whole page with a language model attached, so it resolves an ambiguous
glyph from context — which is exactly what Arabic's dot-distinguished letters
(ب ت ث ن ي) and unwritten short vowels demand.

Their failure mode is the one classical OCR does not have, and it is worse: they
can produce fluent, plausible Arabic that is not on the page. Every intrinsic
quality signal in `metrics` scores a confident hallucination perfectly. That is
why the benchmark measures against known ground truth wherever it can, and why
cross-engine agreement is reported next to it.

Two kinds here. `Qari` is Arabic-specific and runs locally — a fine-tune of
Qwen2/3-VL trained on Arabic documents, small enough for an 8 GB card, and free
per page once downloaded. `Gemini` is a general hosted model that was already
verified exact on Arabic in this project, and is metered.
"""

from __future__ import annotations

from ..base import ArabicExtractor, Page

# Asking for "the text" invites a summary or a preamble. Asking for a
# transcription, and saying what not to add, is what keeps a chat-tuned model
# from being helpful in ways that corrupt the output.
TRANSCRIBE_PROMPT = (
    "Transcribe all the Arabic text in this document image exactly as written, "
    "preserving the original word spacing and line breaks. "
    "Output only the transcription, with no commentary, no translation and no "
    "markdown fences."
)


class QariExtractor(ArabicExtractor):
    """Qari-OCR — Qwen-VL fine-tuned specifically on Arabic documents.

    The interesting local option: purpose-built for this script, 2B parameters
    so it fits an 8 GB card in bf16, and free per page. If it holds up, it is
    the answer for a self-hosted deployment that cannot send documents to an
    API and cannot afford one anyway.
    """

    name = "qari"
    description = "Qari-OCR (Qwen-VL fine-tune, Arabic-specific, local)"
    wants_gpu = True

    #: v0.3 is the 2B Qwen2-VL fine-tune — the largest that fits comfortably in
    #: 8 GB. v0.4 is a 4B Qwen3-VL and wants more card than this machine has.
    DEFAULT_MODEL = "NAMAA-Space/Qari-OCR-v0.3-VL-2B-Instruct"

    _model = None
    _processor = None

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            return False, "transformers/torch are not installed"

        import torch

        if not torch.cuda.is_available():
            return False, "no CUDA device; a 2B VLM on CPU is not worth timing"

        return True, ""

    def warm_up(self) -> None:
        if QariExtractor._model is not None:
            return

        import torch
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

        model_id = self.options.get("model", self.DEFAULT_MODEL)

        QariExtractor._model = Qwen2VLForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=torch.bfloat16, device_map="auto"
        )

        # Cap the vision token budget. Qwen2-VL turns every 28x28 patch into a
        # token, so a 300-dpi A4 page (2480x3500) is ~11k image tokens before a
        # single word is generated — minutes of prefill on an 8 GB card, and an
        # OOM on a busy one. 1.28M pixels is roughly 1600 tokens, which still
        # resolves the dots that separate ب from ت at body-text size.
        QariExtractor._processor = AutoProcessor.from_pretrained(
            model_id,
            min_pixels=self.options.get("min_pixels", 256 * 28 * 28),
            # Raised after a dense book page came back as five characters:
            # 1664 patches downsampled a 274-page scan past the point where
            # the model could find any text at all. This is ~4x that, still
            # well inside an 8 GB card at bf16.
            max_pixels=self.options.get("max_pixels", 6400 * 28 * 28),
        )
        QariExtractor._model.eval()

    def _extract(self, page: Page) -> str:
        import torch

        self.warm_up()

        model, processor = QariExtractor._model, QariExtractor._processor

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": page.image},
                    {"type": "text", "text": TRANSCRIBE_PROMPT},
                ],
            }
        ]

        prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[prompt], images=[page.image], return_tensors="pt").to(model.device)

        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=self.options.get("max_new_tokens", 2048),
                # Greedy: this is transcription, not writing. Sampling here
                # invents plausible Arabic, which is the one failure mode no
                # downstream check would catch.
                do_sample=False,
            )

        # Strip the prompt tokens back off; the model echoes them.
        trimmed = generated[0][inputs.input_ids.shape[1] :]

        return self._strip_markup(processor.decode(trimmed, skip_special_tokens=True))

    @staticmethod
    def _strip_markup(text: str) -> str:
        """Drop the HTML this model is trained to emit.

        Qari v0.3 marks up document structure — `<h1>`, `<h2>`, `<br>` — which
        is a real feature and is not text anyone indexes. Left in, every tag is
        charged as a word the reference does not contain: measured raw, the
        model scored 0.23 WER while its Arabic was letter-perfect. Stripping is
        what the ingestion path would do anyway.
        """
        import re

        without_breaks = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        without_tags = re.sub(r"<[^>]+>", " ", without_breaks)

        return re.sub(r"[ \t]{2,}", " ", without_tags).strip()


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
