"""Deciding whether a page is Arabic, and whether its text can be searched.

The second question is the one this package exists for, and it is not the one
language detection normally answers. Every string below is real Arabic in real
codepoints; the difference between them is whether a query would ever match.
"""

from ocr.language import SPACE_RATIO_HEALTHY, is_arabic, normalize, profile

# One line from ذخائر_لبنان.pdf, in the four states this project's extractors
# actually produce it in. Same sentence every time.
# Repeated to page length on purpose: the spacing checks have an 80-character
# floor so that a two-word heading is never judged mis-segmented, and a single
# line of body text sits under it.
GOOD = "اليسار حينئذ بديدو ومعناه الهاربة. وحدث في أيام بيكماليون أن رامان " * 3
FUSED = "اليسارحينئذ بديدوومعناه الهاربة. وحدثفي أيامبيكماليون أن رامان " * 3
FRAGMENTED = "ا ليسا ر حينئذ بديد و و معنا ه ا لها ر بة. و حد ث في أ يا م بيكماليو ن " * 3
PRESENTATION = "ﺍﻟﻴﺴﺎﺭ ﺣﻴﻨﺌﺬ ﺑﺪﻳﺪﻭ ﻭﻣﻌﻨﺎﻩ ﺍﻟﻬﺎﺭﺑﺔ ﻭﺣﺪﺙ ﻓﻲ ﺃﻳﺎﻡ ﺑﻴﻜﻤﺎﻟﻴﻮﻥ ﺃﻥ ﺭﺍﻣﺎﻥ"


# --- which script is this ------------------------------------------------------


def test_arabic_prose_is_arabic():
    assert is_arabic(GOOD)


def test_english_prose_is_not():
    assert not is_arabic("The quick brown fox jumps over the lazy dog, twice.")


def test_one_arabic_phrase_does_not_make_an_english_page_arabic():
    """A majority of the letters, not merely their presence. Routing an English
    document through an Arabic OCR pipeline because it quotes a phrase would
    make it worse, not better."""
    mostly_english = "The report is titled " + "تقرير" + " and runs to sixty pages."

    assert not is_arabic(mostly_english)


def test_presentation_forms_still_count_as_arabic():
    """They are what a PDF hands over, and refusing to call them Arabic would
    route exactly the pages that need help away from it."""
    assert is_arabic(PRESENTATION)


def test_digits_are_not_letters():
    """A page of tables is not Arabic just because its digits are."""
    assert not is_arabic("2025 42 87.5 1999 300 15")


# --- can the text be searched --------------------------------------------------


def test_healthy_prose_is_usable():
    assert profile(GOOD).is_usable


def test_fused_words_are_detected():
    """pymupdf's raw text layer on this corpus: the producer emitted no space
    glyphs, so clauses run together and no typed word matches."""
    result = profile(FUSED)

    assert result.is_run_together
    assert not result.is_usable
    assert result.space_ratio < SPACE_RATIO_HEALTHY[0]


def test_fragmented_words_are_detected():
    """The word-box path: Arabic's non-joining letters leave gaps inside a word
    and the splitter cuts there. Every character is present and correct, which
    is why a character-level check would call this fine."""
    result = profile(FRAGMENTED)

    assert result.is_fragmented
    assert not result.is_usable
    assert result.space_ratio > SPACE_RATIO_HEALTHY[1]


def test_a_short_string_is_never_judged_fragmented():
    """A heading is two words and a title page is three. Judging spacing on
    them produces noise, so the check has a floor."""
    assert profile("تقرير سنوي").is_usable


def test_presentation_forms_are_flagged_but_not_unusable():
    """A cheap local fix the pipeline already applies. Needing NFKC is not a
    reason to re-read the page with an OCR engine; being fragmented is."""
    result = profile(PRESENTATION)

    assert result.needs_normalization
    assert result.is_usable


# --- normalisation -------------------------------------------------------------


def test_normalize_folds_presentation_forms_to_typed_letters():
    """The whole point: these render identically to typed Arabic and compare
    as different characters, so a query never matches until they are folded."""
    folded = normalize(PRESENTATION)

    assert profile(folded).presentation_forms == 0
    assert "الي" in folded


def test_normalize_strips_bidi_controls():
    assert profile(normalize("‫النص العربي‬")).bidi_controls == 0


def test_normalize_leaves_healthy_text_alone():
    assert normalize(GOOD) == GOOD


def test_normalize_survives_empty_input():
    assert normalize("") == ""


def test_normalize_collapses_runs_of_spaces():
    """Stripping bidi controls leaves the gaps they occupied behind, so the
    runs they create are collapsed in the same pass."""
    assert normalize("النص    العربي") == "النص العربي"
