"""Push every locale prompt into Langfuse as a new version.

The locale files under src/application/prompts/locales/ are the source of
truth. This script makes Langfuse match them:

* a prompt Langfuse has never seen is created and labelled LANGFUSE_PROMPT_LABEL;
* a prompt whose label already points at identical text is skipped;
* a prompt whose text changed gets a new version, labelled the same way.

Edits made in the Langfuse UI are versions too. Running this again only
promotes the file text back to the label when the two differ, so `--dry-run`
first shows what would move.

    cd src && uv run python ../scripts/langfuse_seed_prompts.py [--dry-run] [--label staging]
"""

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from application.prompts.template_parser import (  # noqa: E402
    iter_local_prompts,
    langfuse_name,
)
from shared.utils import get_settings, tracing  # noqa: E402


def main() -> int:
    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default=settings.LANGFUSE_PROMPT_LABEL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = tracing.get_langfuse()
    if client is None:
        print(
            "Langfuse is off: set LANGFUSE_ENABLED=true and both keys in src/.env",
            file=sys.stderr,
        )
        return 1

    created = updated = unchanged = 0

    for lang, group, key, text in iter_local_prompts():
        name = langfuse_name(lang, group, key)

        try:
            current = client.get_prompt(name, label=args.label, cache_ttl_seconds=0, max_retries=0)
            current_text = current.prompt
        except Exception:
            current_text = None

        if current_text == text:
            unchanged += 1
            continue

        action = "create" if current_text is None else "update"
        print(f"{action:6} {name}")

        if not args.dry_run:
            client.create_prompt(
                name=name,
                prompt=text,
                type="text",
                labels=[args.label],
                tags=[lang, group],
                commit_message="Seeded from src/application/prompts/locales",
            )

        if action == "create":
            created += 1
        else:
            updated += 1

    tracing.flush()
    verb = "would change" if args.dry_run else "changed"
    print(f"{verb}: {created} created, {updated} updated; {unchanged} already current (label={args.label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
