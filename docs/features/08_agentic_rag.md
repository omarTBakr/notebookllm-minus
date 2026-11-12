# Grounded chat and agentic answering

## What it does

Answers a question from the notebook's own sources, with citations, streamed token by token. A
notebook with no sources is an ordinary assistant on the same endpoint. Every grounded question
is planned first, and a complex one is researched part by part.

## How it works

`src/application/services/rag/ChatService.py` (`answer_stream`) and `QueryPlanner.py`.

1. **Guard the question** if guardrails are on ([guardrails](10_guardrails.md)).
2. **Fold in memory**: the user's stored `/memory` facts closest to the question
   ([memory](11_memory.md)).
3. **Plan.** One structured call classifies the question (`chitchat`, `simple`, `complex`),
   rewrites it to stand alone without the chat history, and lists the retrieval queries.
4. **Retrieve and rerank.** Each query fetches a wider pool; the reranker keeps the best `top_k`
   ([reranking](09_reranking.md)). Picture hits and linked partners join here
   ([multimodal](14_multimodal_retrieval.md)).
5. **Answer.**
   - `chitchat` skips retrieval and answers as plain chat.
   - `simple` answers in one streamed pass over the numbered documents.
   - `complex` is split into up to `AGENTIC_MAX_SUB_QUERIES` parts. Each part is retrieved,
     reranked and answered with its own citations, in parallel; then one streamed answer combines
     the parts with the original question. The documents are numbered once across all parts, so
     a citation means the same thing everywhere. The UI ticks the parts off as they finish.

The stream is newline-delimited events: `meta` first (grounded flag and citations, so sources
paint before the first token), `step` for a complex question's progress, `thinking` and `delta`
pieces, a `guardrail` event in place of withheld text, then `done`. Only the deltas are stored.

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant R as POST /chat/chats/{id}/message
    participant CC as ChatService
    participant V as Vector store
    participant L as Chat model

    U->>R: { text, image? }
    R->>R: read history (before storing the question,<br/>or it arrives in context twice)
    R->>R: caption an attached picture;<br/>store user turn (text + the caption, never the pixels)
    R->>CC: answer_stream(question, history, lang)
    CC->>V: collection has points?
    alt has documents
        V-->>CC: passages + metadata
        V-->>CC: pictures (project_<id>__img), by text or by the attached picture
        CC->>CC: rerank passages and captions together
        CC->>CC: add each hit's linked partner (its figure / its text)
        CC->>CC: rag prompt + numbered documents<br/>(+ the pictures, for a vision model)
    else no documents
        CC->>CC: plain chat prompt
    end
    CC-->>U: meta { grounded, citations }
    CC->>L: stream
    loop while generating
        L-->>CC: thinking / content pieces
        CC-->>U: thinking · delta
    end
    CC-->>U: done
    R->>R: store assistant turn (answer only)
```

A picture hit carries its caption as text, so the reranker scores passages and captions on one
scale and they compete for the same `top_k`; without a reranker the passages keep `top_k` and
up to `IMAGE_TOP_K` pictures follow. Linked partners come after that, never in place of a
ranked hit, and are capped per answer. In the prompt a partner's heading says which document
it belongs with (`## Document 4 (belongs with Document 2)`). Whether the chat's model can look
at the pictures is read from the catalogue the model picker uses (Ollama's `vision`
capability, OpenRouter's image input, NVIDIA's vision-named models, Claude and Gemini) and
cached; a model without vision gets the captions only.

`chat_id` **is** the `project_id`, so a notebook is its own document space and reuses the
ingestion path unchanged. Vector point ids are `uuid5(asset_id + chunk_order)` — stable
across re-processing, so re-indexing overwrites in place instead of orphaning vectors.

## Settings

| Setting | Does what |
| --- | --- |
| `AGENTIC_RAG_ENABLED` | plan every grounded question; off restores single-pass retrieval on the raw question |
| `AGENTIC_MAX_SUB_QUERIES` | parts a complex question may be split into |
| `AGENTIC_SUBANSWER_MAX_TOKENS` | cap on one part's answer |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_MIN_SCORE` | passages that reach the prompt, and the floor they must clear |
| `CHAT_HISTORY_LIMIT` | prior turns sent as context |

## API

```bash
curl -N -X POST localhost:8000/chat/chats/$CHAT/message \
     -H 'content-type: application/json' -d '{"text": "what does it say about X?"}'
```

| Method | Path | |
| --- | --- | --- |
| `POST` | `/chat/chats/{chat_id}/message` | ask a question; streams events. `image` attaches a picture |
| `GET` | `/chat/chats/{chat_id}/messages` | transcript |

## Tests

`tests/chatting/test_agentic_rag.py`, `test_citations.py`, `tests/api/test_messages.py`.

## Diagrams

[Chat: guardrails → plan → rerank → parts → synthesis](../../software_engineering/3_sequence_diagrams/chat_agentic_rag.png) · [use cases](../../software_engineering/2_use_case_diagrams/chat.png) ·
[application services](../../software_engineering/4_class_diagrams/services.png) · [whole system](../../software_engineering/3_sequence_diagrams/system_overview.png)

## Known limits

- The planner is one more model call before the first token.
- Web-search grounding has no backend yet: [roadmap](../roadmap/web_search_grounding.md).
