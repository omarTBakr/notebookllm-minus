---
name: nlm-release
description: Verify, push, merge and release NotebookLLM-minus. Use when the user asks to push, merge to main, open a PR, make sure tests/pre-commit/GitHub Actions pass, or cut a release.
---

# Verify, push, merge, release

## The target is the public repository
`https://github.com/omarTBakr/notebookllm-minus`, git remote **`upstream`**. Not `origin` (the private, stale `NotebookLLM-minus/notebookllm-minus`). If a request is ambiguous about the remote, it means `upstream`. Releases live there too.

## 1. Verify, reading exit codes
From `src/`:
```bash
uv run pytest -q ../tests -p no:cacheprovider --ignore=../tests/frontend > ../.full_suite.log 2>&1; echo "exit=$?" >> ../.full_suite.log
node --test ../tests/frontend/*.test.mjs; echo "exit=$?"
uv run black --config pyproject.toml --check . ../tests ../evals ../demo/record
uv run ruff check --config pyproject.toml . ../tests ../evals ../demo/record
```
Never pipe the suite through `tail` to judge it: failures hid that way once. The suite takes about ten minutes on this drive; run it in the background. One timer-based guardrail test can fail under disk load; re-run it alone before calling it a real failure. The pre-commit hook (`.githooks/pre-commit`) runs Black and Ruff on staged `src/` files.

## 2. Push and open the PR on upstream
Branches made from `upstream/main` push directly. A branch made from the private history must be replayed first:
```bash
git worktree add ../.nlm-pub-worktree <tip>
cd ../.nlm-pub-worktree && git rebase --onto upstream/main <base on private history>
git diff <tip> HEAD --stat        # must be empty: same files as what was tested
```
Scan the diff for secrets before any public push. Then `git push upstream <branch>`, `gh pr create --repo omarTBakr/notebookllm-minus --base main ...` (body ends with the Claude Code line), check `gh pr view <n> --json mergeable,mergeStateStatus`, and merge with `--merge` only when the user asked.

## 3. GitHub Actions
After a merge, find the run: `gh run list --repo omarTBakr/notebookllm-minus --workflow deploy-main.yml --limit 1`, then `gh run watch <id> --exit-status`. On the public repo the `tests` job must pass; `restart the service` is skipped there by design.

## 4. Release
Named `notebookllm-minus-V<N>.0`, targeting `main`, marked latest. Notes follow the V4.0/V5.0 shape: one-paragraph summary, What's new, Fixed, Changed (removed settings by name), Upgrading (migrations, env lines, models to pull), Known limits. `gh release create ... --notes-file`.
