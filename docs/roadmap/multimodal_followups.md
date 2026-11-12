# Multimodal follow-ups

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

Loose ends from pictures as sources.

## Why / what prompted it

Found while building and live-testing multimodal retrieval.

## Open questions and steps

- [ ] Composer hint: say whether the notebook's model can see pictures before one is attached
- [ ] Keep attached pictures, so a later turn can look again and the transcript can show them
- [ ] Server: Ollama 0.40 or newer, the embedding and caption models pulled, then `MULTIMODAL_ENABLED` in `Docker/env/.env.app`
- [ ] VRAM: the embedder, the caption model and the chat model do not all fit an 8 GB card at once
- [ ] Tables and equations as regions, not only pictures

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[14_multimodal_retrieval](../features/14_multimodal_retrieval.md)
