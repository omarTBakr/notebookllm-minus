# Chunking and citations

## What it does

Cuts extracted text into retrieval-sized chunks without losing where each came from, so every
citation can open its source at the exact page, passage, spreadsheet row or video moment, with
the passage highlighted.

## How it works

### Chunking (`src/application/services/ingest/TextProcessingService.py`)

- `RecursiveCharacterTextSplitter` preferring paragraph, then line, then sentence, then word,
  with a size guard re-splitting anything still over the limit. PDF text often carries no
  sentence punctuation at all. Markdown splits on headings first.
- Chunks below `MIN_CHUNK_CHARS` are merged into a neighbour **on the same page**, never dropped.
  On a 274-page book, 110 of 803 chunks were under 50 characters before this; they embed to
  nothing in particular, and the visible symptom was a grounded answer citing five single words.
- Text is sanitised at extraction: damaged font encodings decode to NUL bytes, which PostgreSQL
  refuses.
- Unicode is normalised to NFKC with bidi controls stripped, so Arabic extracted as presentation
  forms still matches a normally typed query.
- Every chunk keeps `start_index`, its offset in the page text.

### Highlights (`services/ingest/PdfLayoutService.py`)

On `PDF_LOADER=pymupdf` each page is extracted with its word boxes. A chunk's character range is
mapped onto those boxes and stored as `chunk_metadata["highlight"]`: the page size and merged
rectangles. A page whose text was replaced after extraction scales the offsets and is marked
approximate (see [OCR and repair](04_ocr_and_arabic_correction.md)). A video transcript chunk
carries a `time_range` instead; a spreadsheet chunk a `row` and `sheet`.

### Citations (`services/rag/citations.py`)

A search hit knows only `(asset_id, chunk_order)`. `resolve_pages` reads the chunk rows and turns
metadata into a location. **Three things are called a page number and only one is safe to show**:
`chunk_metadata["page"]` is a 0-based index, `page_label` is a display string ("iii", "Cover"),
and the viewer wants a 1-based index. A citation carries `page_number` and `page_label`, and the
`+ 1` happens in this module and nowhere else.

### The viewer

The built-in PDF.js viewer opens the cited page and draws the highlight in the notebook's own
colour. A video citation opens the player at that moment; a spreadsheet citation names the row.

## Settings

| Setting | Does what |
| --- | --- |
| `MIN_CHUNK_CHARS` | below this a chunk is debris; merged into a neighbour on the same page |
| chunk size / overlap | per notebook, from the notebook's settings (default 1000 / 200) |

## API

| Method | Path | |
| --- | --- | --- |
| `GET` | `/chat/chats/{chat_id}/assets/{asset_id}/chunks/{n}/locate` | where a citation sits on its page, and its kind and links |
| `GET` | `/chat/chats/{chat_id}/assets/{asset_id}/content` | the stored file, for the viewer |
| `PATCH` | `/chat/chats/{chat_id}/settings` | chunk size, overlap and highlight colour among others |

## Tests

`tests/ingestion/test_chunking.py`, `test_pdf_layout.py`, `tests/domain/test_chunk_floor.py`,
`tests/chatting/test_citations.py`, `test_page_resolution.py`, `tests/persistence/test_chunk_lookup.py`,
`tests/frontend/citation_label.test.mjs`.

## Diagrams

[chat and citations stories](../../software_engineering/1_user_stories/03_chat_and_citations.png)

## Known limits

- Only pymupdf-extracted PDFs carry highlights; other loaders have no word boxes.
- A highlight on a corrected or OCR'd page is approximate by construction.
