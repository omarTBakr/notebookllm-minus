# Cleanup follow-ups

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

What the 2026-11 cleanup pass left.

## Why / what prompted it

Each was looked at and deferred because it changes behaviour or was not worth the risk in a no-behaviour-change pass.

## Open questions and steps

- [ ] `POST /data/upload/{project_id}` still uses `DataService.store_upload`; moving it to `AssetIngestService.store` adds dedupe and image sniffing, and needs an API test first
- [ ] Late imports inside functions: hoist the ones that guard no cycle and no heavy library
- [ ] `ChatService._answer_complex`: pull the sub-answer task loop into its own method
- [ ] `FLOWER_*` settings are declared in `Settings` but read only by Flower
- [ ] `UserRepository.iter_users` has no caller since `UserService.list_users` went
- [ ] `__all__` lists that re-export names nothing imports
- [ ] The timer-based guardrail test is flaky under disk load; give it a fake clock or a wider bound
- [ ] Answers reach the client in one piece: time to first token equals time to the whole answer in the Ragas and Locust runs of 2026-11-12. Find out whether the output guardrail, nginx or the chat backend is holding the stream

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[16_tests_and_code_quality](../features/16_tests_and_code_quality.md)
