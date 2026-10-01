"""Writing one group as a numbered example directory.

No repo imports here either — plain json/pathlib, testable against a tmp_path
with no PDF involved.
"""

from __future__ import annotations

import json
from pathlib import Path

ROLE_USER = "user"
# Not "assistant": Gemma's own chat template is
# <start_of_turn>model ... <end_of_turn>, and Unsloth's dataset standardiser
# emits exactly this role name for Gemma conversations. See the README.
ROLE_MODEL = "model"


def write_examples(
    groups: list[tuple[list[int], str, str]], pdf_name: str, out_dir: Path, start_at: int
) -> list[dict]:
    """One numbered directory per group, plus the manifest rows for it."""
    entries: list[dict] = []

    for offset, (pages, user_text, model_text) in enumerate(groups):
        example_id = start_at + offset
        example_dir = out_dir / str(example_id)
        example_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "conversations": [
                {"role": ROLE_USER, "content": user_text},
                {"role": ROLE_MODEL, "content": model_text},
            ]
        }

        (example_dir / "conversation.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        entries.append(
            {
                "id": example_id,
                "pdf": pdf_name,
                "pages": pages,
                "user_chars": len(user_text),
                "model_chars": len(model_text),
            }
        )

    return entries


def write_manifest(entries: list[dict], out_dir: Path) -> None:
    (out_dir / "manifest.json").write_text(
        json.dumps({"examples": entries, "count": len(entries)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
