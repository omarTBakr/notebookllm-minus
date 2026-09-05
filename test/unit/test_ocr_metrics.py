"""Scoring an extraction.

The load-bearing test here is the one showing CER and WER disagreeing on
fragmented Arabic. That disagreement is why both are reported: an extractor can
preserve every character and still destroy every word, and only one of the two
numbers notices.
"""

from ocr.benchmark.metrics import (
    agreement,
    character_error_rate,
    levenshtein,
    word_error_rate,
    word_overlap,
)

TRUTH = "اليسار حينئذ بديدو ومعناه الهاربة"
FRAGMENTED = "ا ليسا ر حينئذ بديد و و معنا ه ا لها ر بة"
FUSED = "اليسارحينئذ بديدوومعناه الهاربة"


def test_identical_text_scores_zero():
    assert character_error_rate(TRUTH, TRUTH) == 0.0
    assert word_error_rate(TRUTH, TRUTH) == 0.0


def test_empty_hypothesis_is_total_failure():
    assert character_error_rate(TRUTH, "") == 1.0
    assert word_error_rate(TRUTH, "") == 1.0


def test_fragmentation_is_cheap_on_characters_and_ruinous_on_words():
    """The measurement this package was written to make.

    Splitting `اليسار` into `ا ليسا ر` adds spaces and changes no letters, so
    the character error rate barely moves. Not one of the tokens produced is a
    word anyone will ever search for, so the word error rate collapses — and
    the word error rate is the one that predicts whether retrieval works.
    """
    cer = character_error_rate(TRUTH, FRAGMENTED)
    wer = word_error_rate(TRUTH, FRAGMENTED)

    assert cer < 0.35, "characters are almost all still there"
    assert wer > 0.8, "yet almost no real word survived"
    assert wer > cer * 2


def test_fusing_words_also_wrecks_the_word_rate():
    """The opposite failure, same consequence: `وحدثفي` is not a token any
    query produces."""
    assert word_error_rate(TRUTH, FUSED) > 0.5


def test_a_single_wrong_letter_is_a_small_character_error():
    """Tesseract reading نريار as نيرار — one transposition in a line. It
    should not be scored anywhere near a structural failure."""
    slightly_wrong = TRUTH.replace("بديدو", "بديدى")

    assert character_error_rate(TRUTH, slightly_wrong) < 0.05


def test_normalisation_is_applied_before_comparing():
    """An engine is not worse for emitting presentation forms the pipeline
    folds away anyway — scoring the raw output would punish it for a
    difference that never reaches the index."""
    presentation = "ﺍﻟﻴﺴﺎﺭ"

    assert character_error_rate("اليسار", presentation) == 0.0


def test_levenshtein_basics():
    assert levenshtein("", "") == 0
    assert levenshtein("abc", "abc") == 0
    assert levenshtein("abc", "") == 3
    assert levenshtein("kitten", "sitting") == 3


# --- signals used where there is no ground truth -------------------------------


def test_agreement_is_one_for_identical_and_zero_for_empty():
    assert agreement(TRUTH, TRUTH) == 1.0
    assert agreement(TRUTH, "") == 0.0


def test_agreement_survives_a_small_difference():
    assert agreement(TRUTH, TRUTH.replace("بديدو", "بديدى")) > 0.9


def test_word_overlap_ignores_order():
    """Engines disagree about line order on RTL pages without either being
    wrong about the words, which a whole-string edit distance punishes and
    this does not."""
    reordered = " ".join(reversed(TRUTH.split()))

    assert word_overlap(TRUTH, reordered) == 1.0
    assert agreement(TRUTH, reordered) < 1.0
