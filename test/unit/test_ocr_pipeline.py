"""Choosing between the text layer and OCR.

This is the decision the package exists to make, and the one that reaches
production. The rule it encodes is narrower than "OCR everything", and each
narrowing is here as a test, because each one is a page that would otherwise be
re-read at seconds per page for no gain — or worse, re-read *wrongly*.
"""

import pytest

from ocr.base import ArabicExtractor, Page
from ocr.pipeline import ArabicOcrPipeline, configured_pipeline

GOOD_ARABIC = "اليسار حينئذ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان " * 3
FRAGMENTED = "ا ليسا ر حينئذ بديد و و معنا ه ا لها ر بة. و حد ث في أ يا م بيكماليو ن " * 3
FUSED = "اليسارحينئذ بديدوومعناه الهاربة. وحدثفي أيامبيكماليون أن رامان " * 3
ENGLISH = "The quick brown fox jumps over the lazy dog. " * 4


class FakePage(Page):
    """A Page whose text layer is supplied rather than read from a PDF."""

    def __init__(self, text):
        super().__init__(path="unused.pdf", number=0)
        self.__dict__["text_layer"] = text


class Recording(ArabicExtractor):
    name = "recording"

    def __init__(self, output="نص مستخرج من الصورة بواسطة المحرك الضوئي هنا"):
        super().__init__()
        self.calls = 0
        self.output = output

    def _extract(self, page):
        self.calls += 1
        return self.output


def test_a_usable_arabic_text_layer_is_kept():
    """The expensive path is not taken when the cheap one already works."""
    ocr = Recording()

    decision = ArabicOcrPipeline(ocr).extract(FakePage(GOOD_ARABIC))

    assert not decision.used_ocr
    assert decision.extractor == "text-layer"
    assert ocr.calls == 0, "OCR ran on a page that did not need it"


def test_english_is_never_sent_to_arabic_ocr():
    """Even mangled. An Arabic engine on English text makes it worse, and the
    check that prevents that is script, not quality."""
    ocr = Recording()

    decision = ArabicOcrPipeline(ocr).extract(FakePage(ENGLISH))

    assert not decision.used_ocr
    assert ocr.calls == 0


@pytest.mark.parametrize("broken", [FRAGMENTED, FUSED], ids=["fragmented", "fused"])
def test_unusable_arabic_is_re_read(broken):
    """Both failure modes reach OCR: words split at non-joining letters, and
    words run together because the producer emitted no space glyphs."""
    ocr = Recording()

    decision = ArabicOcrPipeline(ocr).extract(FakePage(broken))

    assert decision.used_ocr
    assert decision.extractor == "recording"
    assert ocr.calls == 1
    assert decision.text == ocr.output


def test_the_returned_text_is_normalised():
    """Whichever path produced it. The caller indexes this string, so it has to
    be in the codepoints a query is written in."""
    presentation = "ﺍﻟﻴﺴﺎﺭ ﺣﻴﻨﺌﺬ ﺑﺪﻳﺪﻭ ﻭﻣﻌﻨﺎﻩ ﺍﻟﻬﺎﺭﺑﺔ ﻭﺣﺪﺙ ﻓﻲ ﺃﻳﺎﻡ ﺑﻴﻜﻤﺎﻟﻴﻮﻥ " * 3

    decision = ArabicOcrPipeline(None).extract(FakePage(presentation))

    assert decision.profile.presentation_forms == 0


def test_an_unusable_page_without_an_extractor_says_so():
    """Rather than silently returning the broken text, which would look like
    success and poison the index."""
    with pytest.raises(RuntimeError, match="configure an available OCR extractor"):
        ArabicOcrPipeline(None).extract(FakePage(FRAGMENTED))


def test_a_failing_extractor_is_not_reported_as_a_result():
    """An engine that returns nothing must not be mistaken for a page that
    contains nothing."""

    class Broken(ArabicExtractor):
        name = "broken"

        def _extract(self, page):
            raise RuntimeError("model file is corrupt")

    with pytest.raises(RuntimeError, match="broken"):
        ArabicOcrPipeline(Broken()).extract(FakePage(FRAGMENTED))


def test_an_empty_extractor_result_is_a_failure_too():
    with pytest.raises(RuntimeError, match="empty output"):
        ArabicOcrPipeline(Recording(output="   ")).extract(FakePage(FRAGMENTED))


def test_configuring_an_unavailable_extractor_names_the_reason():
    """"tesseract is unavailable" and "tesseract is misspelled" are different
    problems, and the message has to distinguish them."""
    with pytest.raises(RuntimeError, match="unavailable"):
        configured_pipeline("no-such-engine")


def test_configuring_an_available_extractor_works():
    """pymupdf-raw needs only what the application already depends on, so this
    is the one engine that can be built anywhere."""
    pipeline = configured_pipeline("pymupdf-raw")

    assert pipeline.extractor.name == "pymupdf-raw"
