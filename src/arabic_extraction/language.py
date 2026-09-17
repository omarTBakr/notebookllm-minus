"""Deciding whether a page is Arabic, and whether its text layer can be trusted.

Two questions, and only the first is what people mean by "language detection".

**Which script is this?** Answered by counting codepoints, not by a language
model. Statistical detectors are trained to tell Arabic from Persian from Urdu,
which is a harder question than the one being asked and one they answer badly on
the short, mangled strings a broken text layer produces. Script is decidable
from the codepoint alone, so it is decided that way — no model, no dependency,
no confidence threshold to tune.

**Is the extracted text usable?** This is the question that actually matters
here, and no language detector answers it. A PDF can yield Arabic that is
perfectly Arabic and still worthless for retrieval, in two distinct ways:

*Presentation forms.* PDF producers embed the per-position glyph variants a font
draws (U+FB50–FDFF, U+FE70–FEFF) rather than the letters someone types
(U+0600–06FF). They render identically and compare as different characters, so a
query never matches. NFKC folds them back, which this project already does.

*Fragmentation.* The remaining problem, and the one that prompted this package.
Arabic letters join, and the non-joining ones (ا ر و د ز ذ) leave gaps inside a
word that a glyph-position extractor reads as spaces: `اليسار` comes out as
`ا ليسا ر`. The text is real Arabic in real codepoints and still cannot be
searched. The opposite failure exists too — extractors that emit no spaces at
all, running whole clauses together.

Both are visible in the *rate* of spacing rather than in any individual word,
which is what `space_ratio` measures and what makes a text layer's quality
judgeable without knowing what the page was supposed to say.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Arabic letters as anyone types them.
ARABIC_BLOCK = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
)

# The glyph variants a font draws. Never typed, never queried, and identical on
# screen to the block above — which is exactly why they are so easy to miss.
PRESENTATION_FORMS = (
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

# Directional overrides. Carry no meaning once the text is a string, and an
# embedding model tokenises them as noise.
BIDI_CONTROLS = frozenset("‎‏‪‫‬‭‮⁦⁧⁨⁩")

# Arabic prose runs about 0.16–0.18 spaces per character: words average five to
# six letters. The bounds below are not guessed — they come from running six
# extractors over pages of this project's own corpus and reading the output:
#
#   tesseract-best   0.177   correct prose
#   pdfplumber       0.198   correctly spaced, but the line is REVERSED
#   pymupdf-words    0.243   `ا ليسا ر`  — split inside words
#   pymupdf-raw      0.115   `وحدثفي`    — no word gaps at all
#
# Hence 0.13 rather than a rounder 0.10: the fused case sits at 0.115, and a
# lower bound beneath it would call the worst text layer in the corpus healthy.
#
# The band cannot catch everything, and pdfplumber is the proof — its spacing is
# perfect and its text is backwards. Spacing measures segmentation, not
# correctness, which is why the benchmark reports cross-engine agreement beside
# it and why neither is trusted alone.
SPACE_RATIO_HEALTHY = (0.13, 0.22)


def _in(codepoint: int, ranges) -> bool:
    return any(low <= codepoint <= high for low, high in ranges)


@dataclass
class ScriptProfile:
    """What a piece of text is made of, and whether it can be searched."""

    characters: int = 0
    arabic: int = 0
    latin: int = 0
    digits: int = 0
    presentation_forms: int = 0
    bidi_controls: int = 0
    spaces: int = 0
    words: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def arabic_ratio(self) -> float:
        """Arabic letters as a share of all letters, presentation forms included."""
        letters = self.arabic + self.latin + self.presentation_forms
        return (self.arabic + self.presentation_forms) / letters if letters else 0.0

    @property
    def is_arabic(self) -> bool:
        """Whether this is Arabic text.

        A majority of the letters, rather than merely any: an English document
        quoting one Arabic phrase is not an Arabic document, and running it
        through an Arabic OCR pipeline would make it worse.
        """
        return self.arabic_ratio >= 0.5

    @property
    def space_ratio(self) -> float:
        return self.spaces / self.characters if self.characters else 0.0

    @property
    def needs_normalization(self) -> bool:
        """Whether NFKC would change what a query can match."""
        return bool(self.presentation_forms or self.bidi_controls)

    @property
    def is_fragmented(self) -> bool:
        """Too many spaces: word-position extraction split inside words."""
        return self.characters > 80 and self.space_ratio > SPACE_RATIO_HEALTHY[1]

    @property
    def is_run_together(self) -> bool:
        """Too few spaces: the producer emitted no word gaps at all."""
        return self.characters > 80 and self.space_ratio < SPACE_RATIO_HEALTHY[0]

    @property
    def is_usable(self) -> bool:
        """Whether this text layer can be searched as it stands.

        The question the ingestion path actually needs answered. Normalisation
        is excluded on purpose — it is a cheap local fix that the pipeline
        already applies, so needing it is not a reason to reach for OCR.
        Fragmentation is, because nothing short of re-reading the page fixes it.
        """
        return not (self.is_fragmented or self.is_run_together)


def profile(text: str) -> ScriptProfile:
    """Count what *text* is made of. Pure counting; no model, no heuristic tuning."""
    result = ScriptProfile()

    if not text:
        return result

    for char in text:
        code = ord(char)

        if char.isspace():
            result.spaces += 1
            continue

        result.characters += 1

        if char in BIDI_CONTROLS:
            result.bidi_controls += 1
        elif _in(code, PRESENTATION_FORMS):
            result.presentation_forms += 1
        elif _in(code, ARABIC_BLOCK):
            if char.isdigit():
                result.digits += 1
            else:
                result.arabic += 1
        elif char.isdigit():
            result.digits += 1
        elif char.isalpha():
            result.latin += 1

    # Spaces are counted above but excluded from `characters`, so add them back
    # for the ratio: it is spaces per character *of running text*.
    result.characters += result.spaces
    result.words = len(text.split())

    if result.presentation_forms:
        result.notes.append(
            f"{result.presentation_forms} presentation-form glyph(s): "
            "typed Arabic will not match this until NFKC is applied"
        )

    if result.bidi_controls:
        result.notes.append(f"{result.bidi_controls} bidi control character(s)")

    if result.is_fragmented:
        result.notes.append(
            f"space ratio {result.space_ratio:.3f} is above "
            f"{SPACE_RATIO_HEALTHY[1]}: words appear split by glyph positioning"
        )

    if result.is_run_together:
        result.notes.append(
            f"space ratio {result.space_ratio:.3f} is below "
            f"{SPACE_RATIO_HEALTHY[0]}: word boundaries appear to be missing"
        )

    return result


def normalize(text: str) -> str:
    """Fold to the codepoints a query is written in.

    Deliberately the same operation as TextProcessingController.normalize_text,
    duplicated here so this package can be used and benchmarked on its own
    without importing the application. If the two ever disagree, that one wins:
    it is the one in the ingestion path.
    """
    if not text:
        return text

    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(dict.fromkeys(map(ord, BIDI_CONTROLS)))

    return re.sub(r"[ \t ]{2,}", " ", folded)


def is_arabic(text: str) -> bool:
    """Whether *text* is predominantly Arabic."""
    return profile(text).is_arabic
