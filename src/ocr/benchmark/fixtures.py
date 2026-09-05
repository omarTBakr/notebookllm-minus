"""Pages whose correct reading is known, so accuracy can be measured exactly.

Every intrinsic signal in `metrics` can be satisfied by output that is fluent,
well-spaced, entirely Arabic and completely invented — which is a real failure
mode of the vision models, not a hypothetical one. The only defence is a page
where the right answer is known in advance.

So these are rendered here from strings this module holds. The text goes in, the
PDF comes out, and whatever an extractor returns can be diffed against the
original. That makes CER and WER exact rather than indicative.

The obvious objection is that a synthetic page is easier than a scan: one font,
no noise, no skew, no columns. True, and the reason the benchmark reports these
*and* real pages side by side. A synthetic score is an upper bound — an engine
that cannot read a clean rendering will not read a book — while the real pages
show which engines degrade and how far.

Rendering uses PIL with libraqm, which does the joining and bidi reordering a
text shaper does. Without it Arabic renders as disconnected isolated letters in
visual order, and the benchmark would be measuring an engine's ability to read
something no real document contains.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Prose rather than word lists: the models under test use context to resolve
# ambiguous glyphs, and a bag of unrelated words removes the very signal that
# distinguishes them from classical OCR.
SAMPLES: dict[str, list[str]] = {
    "prose": [
        "تقرير سنوي عن حالة المكتبة الوطنية",
        "هذا مستند تجريبي لاختبار دقة التعرف الضوئي على الحروف العربية.",
        "تعتمد جودة الاستخراج على الخط المستخدم وعلى دقة الصورة الممسوحة.",
        "يهدف هذا الاختبار إلى قياس نسبة الخطأ في الحروف والكلمات معًا.",
    ],
    # Non-joining letters (ا ر و د ز ذ) are where word-box extractors split
    # inside a word, so a sample dense in them is where fragmentation shows.
    "non_joining": [
        "الدار والدرب وزارة الأوراق",
        "زار وردة ودرس أوراد الدرس",
        "الرزق والوداد وأرواح الأردن",
    ],
    # Digits and Latin mixed into RTL text: the point where bidi handling
    # differs between engines, and where line order goes wrong first.
    "mixed": [
        "البند الأول: مراجعة البيانات المالية لعام 2025.",
        "راجع الصفحة 42 من التقرير المرفق (Annual Report).",
        "بلغت النسبة 87.5% مقارنة بـ 63.2% في العام الماضي.",
    ],
    # Diacritics: dropped by most engines, and worth knowing which.
    "diacritics": [
        "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
        "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ",
    ],
}

FONT_CANDIDATES = (
    "/usr/share/fonts/google-noto-vf/NotoNaskhArabic[wght].ttf",
    "/usr/share/fonts/paktype-naskh-basic-fonts/PakTypeNaskhBasic.ttf",
    "/usr/share/fonts/gnu-freefont/FreeSerif.otf",
    "/usr/share/fonts/almfixed/almfixed.otf",
)


@dataclass
class Fixture:
    """A rendered page and the text it was rendered from."""

    name: str
    path: Path
    truth: str


def _font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate

    raise RuntimeError(
        "no Arabic-capable font found; install google-noto-sans-arabic-fonts "
        f"or one of: {', '.join(FONT_CANDIDATES)}"
    )


def check_shaping() -> tuple[bool, str]:
    """Whether PIL can shape Arabic, i.e. whether libraqm is present.

    Checked explicitly because without it rendering silently produces
    disconnected letters in visual order — a page no real document resembles,
    which would make every number in the benchmark meaningless rather than
    merely wrong.
    """
    try:
        from PIL import features
    except ImportError:
        return False, "Pillow is not installed"

    if not features.check("raqm"):
        return False, "Pillow lacks libraqm, so Arabic would render unshaped"

    try:
        _font_path()
    except RuntimeError as exc:
        return False, str(exc)

    return True, ""


def render(name: str, lines: list[str], directory: Path, dpi: int = 150) -> Fixture:
    """Render *lines* to a one-page PDF and return it with its ground truth."""
    from PIL import Image, ImageDraw, ImageFont

    font_path = _font_path()
    width, margin, leading = 1240, 90, 78

    image = Image.new("RGB", (width, margin * 2 + leading * len(lines)), "white")
    draw = ImageDraw.Draw(image)

    title = ImageFont.truetype(font_path, 44)
    body = ImageFont.truetype(font_path, 32)

    y = margin
    for index, line in enumerate(lines):
        draw.text(
            (width - margin, y),
            line,
            font=title if index == 0 else body,
            fill="black",
            # anchor="ra" right-aligns, which is where Arabic body text sits;
            # direction/language drive libraqm's shaping and bidi reordering.
            anchor="ra",
            direction="rtl",
            language="ar",
        )
        y += leading

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.pdf"
    image.save(path, "PDF", resolution=dpi)

    return Fixture(name=name, path=path, truth="\n".join(lines))


def build_all(directory: Path) -> list[Fixture]:
    """Render every sample. Raises if Arabic cannot be shaped."""
    ok, reason = check_shaping()

    if not ok:
        raise RuntimeError(f"cannot render Arabic fixtures: {reason}")

    return [render(name, lines, directory) for name, lines in SAMPLES.items()]
