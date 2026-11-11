# Multimodal retrieval: pictures as sources

## What it does

Makes the pictures in a notebook searchable and citable. Figures inside PDFs are found at ingest,
PNG, JPEG and WebP files can be added as sources, and a question can carry a picture of its own.
A question in English or Arabic finds the chart that answers it; a vision model is shown the
cited figures, and any other model reads their captions. Figures travel with the text that
explains them.

Everything here is behind `MULTIMODAL_ENABLED`. Off leaves the pipeline exactly as it was.

## How it works

### At ingest

1. **Find** (`src/application/services/ingest/ImageRegionService.py`, parse stage). Records
   *where* each figure sits: page, bounding box, the PDF's own image id. Icons and bullets (too
   small, or under 2% of the page) and logos (the same picture on more than three pages) are
   skipped. A page still empty after OCR but with something drawn on it becomes one picture.
2. **Caption** (`ImageCaptionService.py`, postprocess stage). A vision model writes a short
   caption for every picture, given the text printed next to it as context. With no caption
   model, or when it fails, the nearest text on the page ("Figure 3: …") is the caption.
3. **Chunk** (assemble stage). Every figure becomes an **image chunk** after the text chunks,
   shaped like a text chunk (`page`, a `highlight` in the same coordinate space, the caption as
   its content, `kind: "image"`), so citations, `/locate` and the viewer's highlight box need no
   special case. The pixels are not stored; they are cut back out of the asset when needed
   (`render_chunk`, cached).
4. **Link** (`ChunkLinker.py`). A passage that names the figure's label ("Figure 3", "Fig. 3",
   "الشكل ٣", Arabic-Indic digits included) links across pages; on the same page, passages
   directly above or below the figure link by distance. The best few on each side are written
   into `chunk_metadata` as `linked_images` and `linked_chunks`. No migration: it is JSON.
5. **Embed** (`src/application/services/rag/ImageIndexService.py`, `index_images_task`). Each
   picture is embedded with **EmbeddingGemma 2**, which puts text and images in one 768-d space,
   into a second collection per notebook, `project_<id>__img`. The embedder is global, not per
   chat, so a chat's own text embedder never touches it. The caption is indexed with the text
   too, so a plain text search finds a figure by what it says.

### At answer time

- **Two searches.** The question (or the attached picture) is embedded with the image embedder
  and searched against `project_<id>__img`, beside the ordinary text search.
- **One ranking.** A picture hit carries its caption as text, so the reranker scores passages and
  captions on one scale and they compete for the same `top_k`. Without a reranker the passages
  keep `top_k` and up to `IMAGE_TOP_K` pictures follow.
- **Linked partners** (`services/rag/links.py`, `expand_links`). A retrieved passage brings its
  figure and a retrieved figure brings the passage that explains it. Partners come after the
  ranked hits, never in place of one, capped per answer; in the prompt a partner's heading says
  which document it belongs with (`## Document 4 (belongs with Document 2)`), and the citation
  list shows it as "↳ with [n]".
- **The vision gate** (`services/llm/vision.py`). Whether the chat's model can look at pictures
  is read from the catalogue the picker uses and cached for an hour. A vision model receives up
  to `IMAGE_MAX_PER_ANSWER` pictures; any other model gets the captions only.
- **A picture in the question.** Checked, re-encoded and captioned before anything is stored.
  What the transcript keeps is text: the question plus a note of what the picture showed.

### The embedder

`src/application/providers/embedding/image/`: `OllamaImageEmbeddingProvider` (the host daemon) and
`LlamaCppImageEmbeddingProvider` (a `llama-server` with an mmproj). **Ollama's `/api/embed` takes
a picture as `{"input": [{"image": "<base64>"}]}`.** This shape is undocumented, and a
top-level `images` field is accepted and silently returns nothing.

## Settings

| Setting | Does what |
| --- | --- |
| `MULTIMODAL_ENABLED` | everything on this page |
| `IMAGE_EMBEDDING_BACKEND` / `IMAGE_EMBEDDING_MODEL_ID` / `IMAGE_EMBEDDING_SIZE` | `ollama` or `llamacpp`; `embeddinggemma-2:440m-q8_0-text-vision`; 768 |
| `IMAGE_EMBEDDING_URL` / `IMAGE_EMBEDDING_TIMEOUT_S` | the llama.cpp server, and the per-request timeout |
| `IMAGE_CAPTION_MODEL_ID` | the vision model that captions at ingest (`local/gemma3:4b`); empty = nearest text |
| `IMAGE_MIN_SIDE_PX` / `IMAGE_MIN_AREA_RATIO` / `IMAGE_MAX_REPEAT` | what counts as a figure |
| `IMAGE_MAX_SIDE_PX` | longest side a picture is scaled to before embedding, captioning or showing |
| `IMAGE_TOP_K` / `IMAGE_MIN_SCORE` / `IMAGE_MAX_PER_ANSWER` | picture hits kept per query, their floor, and how many reach a vision model |
| `IMAGE_QUERY_MAX_BYTES` | largest picture accepted from the composer |
| `LINKS_ENABLED` | figure and passage links |
| `LINK_MAX_TEXT_PER_IMAGE` / `LINK_MAX_IMAGES_PER_TEXT` | partners remembered per chunk at ingest |
| `LINK_MAX_IMAGES_PER_ANSWER` / `LINK_MAX_PASSAGES_PER_ANSWER` | partners added to one answer |

Needs `ollama pull embeddinggemma-2:440m-q8_0-text-vision` on an Ollama **0.40 or newer**.

## API

| Method | Path | |
| --- | --- | --- |
| `POST` | `/chat/chats/{chat_id}/message` | `image`: a data URL (PNG, JPEG, WebP) attached to the question |
| `POST` | `/chat/chats/{chat_id}/documents` | accepts pictures as sources while multimodal is on |
| `GET` | `/chat/chats/{chat_id}/assets/{asset_id}/chunks/{n}/image` | the picture an image chunk shows (`size` caps the longest side); 404 for a text chunk |
| `GET` | `/chat/chats/{chat_id}/assets/{asset_id}/chunks/{n}/locate` | `kind`, `linked_images`, `linked_chunks` |

## Data

Image chunk metadata: `kind`, `page`, `bbox`, `highlight`, `nearby_text`, `linked_chunks`. A
passage gains `linked_images`. Vectors: `project_<id>__img`.

## Tests

`tests/ingestion/test_images.py`, `test_chunk_links.py`, `tests/chatting/test_multimodal.py`,
`test_links.py`, `tests/api/test_images.py`, `tests/frontend/attachment.test.mjs`,
`citation_label.test.mjs`.

## Diagrams

[Pictures at ingest](../../software_engineering/3_sequence_diagrams/image_ingest.png) · [A question with pictures](../../software_engineering/3_sequence_diagrams/image_query_answer.png) ·
[class diagram](../../software_engineering/4_class_diagrams/multimodal.png) · [use cases](../../software_engineering/2_use_case_diagrams/multimodal.png) · [user stories](../../software_engineering/1_user_stories/08_multimodal.png)

## Known limits

See [multimodal follow-ups](../roadmap/multimodal_followups.md): attached pictures are not kept,
the composer does not say whether the current model has vision, and the server needs Ollama 0.40
and the model pulled before the switch is turned on.
