"""The extractor contract, and the registry that decides what can run.

None of these install an OCR engine. They pin the behaviour that makes a
comparison across a dozen engines readable: one engine failing never stops the
rest, and an engine that could not run is reported as such rather than as an
engine that did badly. Those two are constantly confused when reading a
benchmark table, and the difference is the whole point of `available()`
returning a reason.
"""

import pytest

from arabic_extraction.base import ArabicExtractor, Extraction, Page
from arabic_extraction.registry import build, survey


class Working(ArabicExtractor):
    name = "working"

    def _extract(self, page):
        return "اليسار حينئذ بديدو ومعناه الهاربة"


class Exploding(ArabicExtractor):
    name = "exploding"

    def _extract(self, page):
        raise RuntimeError("the model file is corrupt")


class Empty(ArabicExtractor):
    name = "empty"

    def _extract(self, page):
        return "   "


@pytest.fixture
def page(tmp_path):
    """A Page that is never opened — these tests never touch a PDF."""
    return Page(path=tmp_path / "nothing.pdf", number=0)


def test_a_successful_run_is_timed_and_marked_ok(page):
    result = Working().run(page)

    assert result.ok
    assert result.extractor == "working"
    assert result.seconds >= 0
    assert not result.error


def test_a_failing_extractor_is_recorded_not_raised(page):
    """One engine dying must not end a run over twelve of them — the failure is
    a row in the table, which is also where it is most useful."""
    result = Exploding().run(page)

    assert not result.ok
    assert "RuntimeError" in result.error
    assert "corrupt" in result.error
    assert result.text == ""


def test_whitespace_output_is_not_success(page):
    """An engine that returns nothing has failed, however calmly it did so."""
    assert not Empty().run(page).ok


def test_the_result_carries_the_normalised_text():
    """Comparisons are made on what the index would store, so an engine is
    neither rewarded nor punished for emitting presentation forms that the
    pipeline folds away anyway."""
    extraction = Extraction(extractor="x", text="ﺍﻟﻴﺴﺎﺭ", seconds=0.0, page=0)

    assert extraction.profile.presentation_forms == 0
    assert extraction.normalized != extraction.text


# --- the registry --------------------------------------------------------------


def test_every_known_extractor_reports_availability():
    """Including the ones that cannot run: a name missing from the table is
    read as an engine that scored nothing, not one that was never installed."""
    entries = survey()

    assert entries
    for entry in entries:
        assert entry.name
        assert isinstance(entry.ok, bool)
        if not entry.ok:
            assert entry.reason, f"{entry.name} is unavailable without saying why"


def test_the_text_layer_extractors_are_always_available():
    """They need only pymupdf, which the application already depends on, so a
    comparison always has a baseline to measure OCR against."""
    available = {e.name for e in survey() if e.ok}

    assert {"pymupdf-raw", "pymupdf-words"} <= available


def test_build_returns_only_what_can_run():
    for extractor in build():
        ok, _ = type(extractor).available()
        assert ok


def test_build_can_be_narrowed_to_named_extractors():
    built = build(["pymupdf-raw"])

    assert [e.name for e in built] == ["pymupdf-raw"]


def test_an_availability_check_that_raises_counts_as_unavailable(monkeypatch):
    """A probe that throws — a broken install, a missing shared library — must
    not take the whole survey down with it."""
    import arabic_extraction.registry as registry

    class Detonating(ArabicExtractor):
        name = "detonating"

        @classmethod
        def available(cls):
            raise OSError("libcudart.so.12: cannot open shared object file")

        def _extract(self, page):
            return ""

    monkeypatch.setattr(registry, "ALL_EXTRACTORS", (Detonating,))

    entry = registry.survey()[0]

    assert not entry.ok
    assert "libcudart" in entry.reason


def test_cli_without_corpus_does_not_scan_current_directory(monkeypatch, tmp_path):
    """An unset OCR_CORPUS means synthetic-only, not ``Path('.')``."""
    import arabic_extraction.benchmark.__main__ as cli

    monkeypatch.delenv("OCR_CORPUS", raising=False)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["--list"]) == 0


# --- a model that returns more than text ---------------------------------------


def test_qari_markup_is_stripped_before_scoring():
    """Qari v0.3 is trained to mark up document structure as HTML, which is a
    feature of the model and was a bug in the comparison: measured on the raw
    output it scored 0.23 WER while its Arabic was letter-perfect, because
    every tag counted as words the reference did not contain."""
    from arabic_extraction.extractors.vision_models import QariExtractor

    raw = "<h1>تقرير سنوي</h1><br><h2>هذا مستند تجريبي لاختبار الدقة</h2>"

    stripped = QariExtractor._strip_markup(raw)

    assert "<" not in stripped and ">" not in stripped
    assert "تقرير سنوي" in stripped
    assert "هذا مستند تجريبي" in stripped


def test_a_line_break_tag_becomes_a_line_break():
    """Not a space: the tag is where the model says one line ended and the
    next began, and collapsing that loses the page's structure entirely."""
    from arabic_extraction.extractors.vision_models import QariExtractor

    stripped = QariExtractor._strip_markup("<p>سطر أول</p><br><p>سطر ثان</p>")

    assert len(stripped.splitlines()) == 2


def test_plain_text_passes_through_untouched():
    """Every other engine returns plain text, and must not be reshaped by a
    rule that exists for one model."""
    from arabic_extraction.extractors.vision_models import QariExtractor

    plain = "اليسار حينئذ بديدو ومعناه الهاربة"

    assert QariExtractor._strip_markup(plain) == plain


# --- a hosted model, over OpenRouter ------------------------------------------


class TestOpenRouter:
    """OpenRouter fronts many vision models behind one wire format, so the model
    id is configuration. The class therefore has to defend against being pointed
    at a model that cannot see."""

    def _extractor(self, monkeypatch, key="k", model=""):
        from arabic_extraction.extractors.hosted import OpenRouterExtractor

        monkeypatch.setenv("OPENROUTER_API_KEY", key)
        if model:
            monkeypatch.setenv("OPENROUTER_MODEL", model)
        else:
            monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
        return OpenRouterExtractor

    def test_without_a_key_it_says_so_rather_than_failing_later(self, monkeypatch):
        """Both sources have to be empty: the extractor reads the environment
        first and falls back to Settings, so clearing only the env var still
        finds the key that .env supplies on a developer machine."""
        from utils import get_settings

        cls = self._extractor(monkeypatch, key="")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(get_settings(), "OPENROUTER_API_KEY", "", raising=False)

        ok, reason = cls.available()

        assert not ok
        assert "OPENROUTER_API_KEY" in reason

    def test_the_default_model_is_used_when_none_is_configured(self, monkeypatch):
        cls = self._extractor(monkeypatch)

        _, model = cls._credentials()

        assert model == cls.DEFAULT_MODEL

    def test_a_text_only_model_is_refused(self, monkeypatch):
        """A text-only model does not refuse an image politely — it ignores it
        and describes a page it never saw, which scores as a bad reader rather
        than a wrong configuration."""
        cls = self._extractor(monkeypatch, model="some/text-only")
        _fake_catalogue(monkeypatch, {"some/text-only": ["text"]})

        ok, reason = cls.available()

        assert not ok
        assert "does not accept images" in reason

    def test_a_vision_model_is_accepted(self, monkeypatch):
        cls = self._extractor(monkeypatch, model="some/vision")
        _fake_catalogue(monkeypatch, {"some/vision": ["text", "image"]})

        ok, reason = cls.available()

        assert ok, reason

    def test_a_model_that_does_not_exist_is_refused(self, monkeypatch):
        cls = self._extractor(monkeypatch, model="nobody/nothing")
        _fake_catalogue(monkeypatch, {"some/vision": ["text", "image"]})

        ok, reason = cls.available()

        assert not ok
        assert "not on OpenRouter" in reason

    def test_an_unreachable_catalogue_does_not_block_the_run(self, monkeypatch):
        """Being offline is not the same as being misconfigured. The check is a
        courtesy; refusing to run because it failed would be worse than the
        error it prevents."""
        import urllib.request

        cls = self._extractor(monkeypatch, model="some/vision")
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no network")),
        )

        ok, reason = cls.available()

        assert ok
        assert "could not verify" in reason

    def test_a_fenced_transcription_is_unwrapped(self):
        """Charging a markdown fence to a model's error rate measures
        instruction-following, not reading — the mistake that cost Qari a factor
        of four before its markup was stripped."""
        from arabic_extraction.extractors.hosted import _strip_fences

        assert _strip_fences("```\nاليسار حينئذ\n```") == "اليسار حينئذ"
        assert _strip_fences("```text\nاليسار\n```") == "اليسار"
        assert _strip_fences("اليسار حينئذ") == "اليسار حينئذ"
        # A fence *inside* a page is content, not wrapping.
        assert "```" in _strip_fences("قال\n```code```\nثم")


def _fake_catalogue(monkeypatch, models: dict):
    """Stand in for openrouter.ai/api/v1/models."""
    import io
    import json
    import urllib.request

    body = {
        "data": [
            {"id": name, "architecture": {"input_modalities": mods}}
            for name, mods in models.items()
        ]
    }

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: _Response(json.dumps(body).encode()),
    )
