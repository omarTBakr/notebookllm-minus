"""Pairing pymupdf and tesseract-best output into training examples.

No repo imports here on purpose — this is pure list-of-strings logic, testable
without a PDF, an extractor, or src/ on the path.
"""

from __future__ import annotations


def group_pages(
    damaged: list[str], corrected: list[str], min_chars: int
) -> list[tuple[list[int], str, str]]:
    """Pair pages 1:1, merging forward while the damaged side is too short.

    Groups rather than splits: tesseract-best OCRs a whole rendered page in
    one call, so there is nothing inside a page to cut its output against.
    A page whose pymupdf text is too short to be a useful example on its own
    — a plate, a half-blank page, a running header caught alone — is merged
    into the pages after it instead of becoming a degenerate standalone
    example, the same shape ``MIN_CHUNK_CHARS`` merging already uses
    elsewhere in this codebase (``TextProcessingController.merge_undersized``).

    A page where *either* side is empty after stripping is skipped outright
    rather than flushing what was pending: there is nothing to teach the
    model from a page neither extractor could read, and dropping it should
    not sever an in-progress merge of the pages around it. The 0-based page
    indices recorded per group are honest about the resulting gap.
    """
    groups: list[tuple[list[int], str, str]] = []
    pending_pages: list[int] = []
    pending_damaged: list[str] = []
    pending_corrected: list[str] = []

    def flush() -> None:
        if not pending_pages:
            return
        groups.append(
            (
                list(pending_pages),
                "\n\n".join(pending_damaged).strip(),
                "\n\n".join(pending_corrected).strip(),
            )
        )
        pending_pages.clear()
        pending_damaged.clear()
        pending_corrected.clear()

    for index, (raw_damaged, raw_corrected) in enumerate(zip(damaged, corrected)):
        stripped_damaged = raw_damaged.strip()
        stripped_corrected = raw_corrected.strip()

        if not stripped_damaged or not stripped_corrected:
            continue

        pending_pages.append(index)
        pending_damaged.append(stripped_damaged)
        pending_corrected.append(stripped_corrected)

        if sum(len(text) for text in pending_damaged) >= min_chars:
            flush()

    flush()  # a trailing short group still ships rather than being lost

    return groups
