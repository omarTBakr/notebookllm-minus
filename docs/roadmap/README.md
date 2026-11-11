# Roadmap

Planned work, one placeholder page each. A page keeps the same headings a feature page will need
(**What**, **Why**, **Open questions and steps**, **Design**, **Settings, API, data**, **Tests**),
with `TODO` where nothing is decided. When a feature ships, its page moves to
[`../features/`](../README.md) and gets a number.

| Planned | What |
| --- | --- |
| [Web-search grounding](web_search_grounding.md) | Answer from the web as well as from a notebook's sources, with citations to the pages used. |
| [Private Google Sheets and more formats](private_google_sheets_and_more_formats.md) | Read a Google Sheet that is not public, and accept formats beyond PDF, txt, markdown, CSV, XLSX and pictures (docx, pptx, epub, html files). |
| [Studio: audio overview, slides, video, reports, infographic, data table](studio_audio_slides_video_reports.md) | The Studio tiles that are placeholders today. |
| [Interface: copy, share, apps, sort](ui_copy_share_apps_sort.md) | Sharing a notebook, sorting and filtering sources and notebooks, and the remaining toolbar actions. |
| [OCR: several pages per call](ocr_batch_pages_per_call.md) | Send several pages to the OCR or correction model in one call. |
| [Arabic re-read in batched ingest](arabic_reread_in_batched_ingest.md) | Re-read pages whose Arabic text is present but badly spaced with an OCR engine or qalam, per page and only when broken. |
| [A guard for qalam's over-return bug](qalam_overreturn_guard.md) | Detect a page where qalam returns far too much text and fall back. |
| [Memory on the Mongo backend](memory_on_mongo.md) | Implement `personal_user_info()` for MongoDB so `/memory` works on both backends. |
| [Multimodal follow-ups](multimodal_followups.md) | Loose ends from pictures as sources. |
| [Deployment follow-ups](deployment_followups.md) | One-time and environment-specific steps that are not in the repository. |
| [Cleanup follow-ups](cleanup_followups.md) | What the 2026-11 cleanup pass left. |
