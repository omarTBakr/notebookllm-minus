"""Scoring an extraction, with and without something to compare it against.

The comparison has two halves, because neither alone is trustworthy.

**With ground truth.** Synthetic pages are rendered from strings this package
wrote, so CER and WER are exact. That is the only way to say one engine is
*more accurate* than another rather than merely different. The catch is that
synthetic pages are clean — one font, no scan noise, no columns — so a ranking
from them alone flatters engines that fall apart on real documents.

**Without it.** Real pages have no reference, and producing one by hand for a
274-page book is not on. What can still be measured is whether the output has
the *shape* of searchable Arabic: right script, no presentation forms, word
spacing in the band real prose occupies. These do not prove an engine read the
words correctly, and a fluent hallucination scores perfectly on all of them —
which is precisely why they are reported beside the exact scores and never
instead of them.

Both are needed. An engine that wins on synthetic pages and produces
0.26-spaces-per-character mush on the real ones has told you something, and so
has the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..language import normalize, profile


def levenshtein(a: str, b: str) -> int:
    """Edit distance, two rows at a time.

    Written out rather than pulled from a dependency: it is fifteen lines, and
    the benchmark should not need a package installed to report a number.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # Iterate over the shorter string to keep the row small.
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))

    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        previous = current

    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Edit distance over the reference length. 0.0 is perfect; >1.0 is possible."""
    reference, hypothesis = normalize(reference), normalize(hypothesis)

    if not reference:
        return 0.0 if not hypothesis else 1.0

    return levenshtein(reference, hypothesis) / len(reference)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """The same, over whitespace-separated tokens.

    Reported alongside CER because on Arabic they disagree in a way that is the
    whole point of this exercise: an extractor that splits `اليسار` into
    `ا ليسا ر` has a *low* CER — every character is present and correct — and a
    catastrophic WER, because not one of the words it produced is a word. WER is
    the number that reflects whether retrieval will work.
    """
    reference_words = normalize(reference).split()
    hypothesis_words = normalize(hypothesis).split()

    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0

    # Levenshtein over token sequences: map each distinct word to one character
    # so the string implementation above can be reused directly.
    vocabulary: dict[str, str] = {}

    def encode(words):
        return "".join(vocabulary.setdefault(word, chr(0xE000 + len(vocabulary))) for word in words)

    return levenshtein(encode(reference_words), encode(hypothesis_words)) / len(reference_words)


@dataclass
class Score:
    """One extraction's quality, from whichever angles were available."""

    extractor: str
    seconds: float
    peak_rss_mb: float | None = None
    rss_delta_mb: float | None = None
    peak_gpu_mb: float | None = None
    cpu_seconds: float | None = None

    # Exact, and only when a reference exists.
    cer: float | None = None
    wer: float | None = None

    # Intrinsic, always available.
    arabic_ratio: float = 0.0
    space_ratio: float = 0.0
    presentation_forms: int = 0
    characters: int = 0
    words: int = 0
    usable: bool = False
    notes: tuple[str, ...] = ()

    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error) or self.characters == 0


def score(extraction, reference: str | None = None) -> Score:
    """Measure one extraction, against *reference* when there is one."""
    text_profile = extraction.profile

    result = Score(
        extractor=extraction.extractor,
        seconds=extraction.seconds,
        peak_rss_mb=extraction.peak_rss_mb,
        rss_delta_mb=extraction.rss_delta_mb,
        peak_gpu_mb=extraction.peak_gpu_mb,
        cpu_seconds=extraction.cpu_seconds,
        arabic_ratio=text_profile.arabic_ratio,
        space_ratio=text_profile.space_ratio,
        presentation_forms=text_profile.presentation_forms,
        characters=text_profile.characters,
        words=text_profile.words,
        usable=text_profile.is_usable,
        notes=tuple(text_profile.notes),
        error=extraction.error,
    )

    if reference is not None and extraction.ok:
        result.cer = character_error_rate(reference, extraction.text)
        result.wer = word_error_rate(reference, extraction.text)

    return result


def agreement(a: str, b: str) -> float:
    """How much two extractions of the same page agree, 0.0–1.0.

    Used where there is no ground truth: if several independent engines
    converge on the same reading, that reading is probably what the page says.
    It is a weak signal on its own — two engines sharing an architecture share
    its mistakes — so it corroborates rather than decides.
    """
    a, b = normalize(a), normalize(b)

    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    return max(0.0, 1.0 - levenshtein(a, b) / max(len(a), len(b)))


def word_overlap(a: str, b: str) -> float:
    """Share of *a*'s distinct words that also appear in *b*.

    Complements `agreement`, which a whole-string edit distance makes
    pessimistic when two engines read the same words in a different order —
    common on RTL text, where line order and bidi handling differ between
    engines without either being wrong about the words.
    """
    words_a = set(normalize(a).split())
    words_b = set(normalize(b).split())

    if not words_a:
        return 0.0

    return len(words_a & words_b) / len(words_a)


__all__ = [
    "Score",
    "agreement",
    "character_error_rate",
    "levenshtein",
    "score",
    "word_error_rate",
    "word_overlap",
    "profile",
]
