"""Prompts for the ingestion pipeline, one package per language.

Layout is ``prompts/<lang>/<group>.py``, holding module-level strings rendered
with ``str.format`` — the same shape as ``templates/locales``, which serves the
chat and studio prompts.

They are kept apart deliberately. ``templates.locales`` answers *a user*: its
prompts are written per conversation, resolved from the chat's own ``lang``,
and a missing key there raises rather than silently answering an Arabic
question in English. These answer *a document*, during ingestion, where there
is no conversation and no user waiting — the language is a property of the page
being repaired, and falling back to English is the right behaviour rather than
a bug. Mixing the two would mean one resolver serving two different ideas of
what "language" means.

    get_prompt("ocr_correction", "system_prompt")
    get_prompt("ocr_correction", "page_prompt", {"page": 1, "text": "..."})
"""

import importlib

from shared.utils import get_logger

logger = get_logger(__name__)

DEFAULT_LANG = "en"

# Cached rather than re-imported per page. importlib caches too; this keeps the
# hot path -- once per page, per document -- a dict lookup.
_MODULES: dict[tuple[str, str], object] = {}


def _module(group: str, lang: str):
    """Import (and cache) one prompt group for one language."""
    cached = _MODULES.get((lang, group))

    if cached is not None:
        return cached

    module = importlib.import_module(f"application.ocr_prompts.{lang}.{group}")
    _MODULES[(lang, group)] = module

    return module


def get_prompt(group: str, key: str, vars: dict | None = None, lang: str | None = None) -> str:
    """Return the prompt at *group*.*key* in *lang*, formatted with *vars*.

    Falls back to English when the language has no such group. Unlike the chat
    templates this is not a loud failure: a page in a language nobody has
    written prompts for should still be repaired with the English instructions,
    which work — the page's own text is what carries the language, and the
    model sees it either way.

    A *key* missing from a group that does exist still raises, because that is
    a typo or a half-finished translation rather than a language gap.
    """
    resolved = (lang or DEFAULT_LANG).strip().lower()

    try:
        module = _module(group, resolved)

    except ModuleNotFoundError:
        if resolved == DEFAULT_LANG:
            raise

        logger.warning("No %r prompts for language %r; using %r", group, resolved, DEFAULT_LANG)
        module = _module(group, DEFAULT_LANG)

    try:
        template = getattr(module, key)

    except AttributeError as exc:
        raise AttributeError(f"Prompt {key!r} is missing from prompts/{resolved}/{group}.py") from exc

    return template.format(**vars) if vars else template


__all__ = ["DEFAULT_LANG", "get_prompt"]
