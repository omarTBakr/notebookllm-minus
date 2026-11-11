# Arabic re-read in batched ingest

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

Re-read pages whose Arabic text is present but badly spaced with an OCR engine or qalam, per page and only when broken.

## Why / what prompted it

This existed (`OCR_ENABLED`, `OCR_EXTRACTOR`, `OCR_MIN_CHARS`, `ProcessService._reread_unusable_arabic`) and was removed on 2026-11-11: the batched pipeline never called it, so the settings did nothing. `arabic_extraction.language.profile()`, which made the per-page decision (Arabic, unusable, long enough), is still there. The removed code is in git history before commit `Remove code nothing calls`.

## Open questions and steps

- [ ] Whether it is still wanted now that the correction pass keeps highlights and the re-read does not
- [ ] If so: call it from the parse stage beside `ocr_unreadable_pages`, record the length ratio per page, and report progress per page

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[04_ocr_and_arabic_correction](../features/04_ocr_and_arabic_correction.md)
