# Memory on the Mongo backend

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

Implement `personal_user_info()` for MongoDB so `/memory` works on both backends.

## Why / what prompted it

`DbProvider.personal_user_info()` raises `NotImplementedError` anywhere but Postgres.

## Open questions and steps

- [ ] A Mongo repository and its place in the backend contract test
- [ ] Listing and forgetting facts from the UI, on both backends
- [ ] Tests for `/memory`, which has none

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[11_memory](../features/11_memory.md)
