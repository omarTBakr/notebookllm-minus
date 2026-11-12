# A guard for qalam's over-return bug

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

Detect a page where qalam returns far too much text and fall back.

## Why / what prompted it

qalam 0.1.1 returns 200 to 300 times too much text on some documents, with `confidence: 1.0`. A relative guard does not work: on the measured document 99.6% of pages are affected, so the median is itself bloated.

## Open questions and steps

- [ ] An absolute bound per page, or a cross-check against `pymupdf-raw`
- [ ] Only matters if qalam's text is used again; today the application only asks it which pages need OCR

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[04_ocr_and_arabic_correction](../features/04_ocr_and_arabic_correction.md)
