# OCR: several pages per call

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

Send several pages to the OCR or correction model in one call.

## Why / what prompted it

Measured 2 to 4 times cheaper per page, and still not in the production path.

## Open questions and steps

- [ ] Page numbers in a batched reply must be batch-local: a printed page number will hijack an index and misfile pages
- [ ] Where the batch size is set, and how a partial failure is retried

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[04_ocr_and_arabic_correction](../features/04_ocr_and_arabic_correction.md)
