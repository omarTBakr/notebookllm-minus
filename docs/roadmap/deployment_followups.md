# Deployment follow-ups

> **Placeholder.** Planned, not built. The headings are fixed; fill them in when the work starts.

## What

One-time and environment-specific steps that are not in the repository.

## Why / what prompted it

Each has bitten a deploy once.

## Open questions and steps

- [ ] The server's systemd unit still passes `--env-file`; its `ExecStart` must become `docker compose up -d --build`
- [ ] YouTube blocks cloud IPs: set `YOUTUBE_PROXY_URL` on a hosted server
- [ ] Most NVIDIA NIM catalogue models answer 404 for a given key; pick models the account can call
- [ ] Docker Desktop refuses new bind mounts after days of uptime until it is restarted
- [ ] Link sources need migration `0012` and a rebuild on an older deployment

## Design

TODO

## Settings, API, data

TODO

## Tests

TODO

## Related

[15_deployment](../features/15_deployment.md)
