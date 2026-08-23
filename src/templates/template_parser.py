"""Loads prompt strings out of the per-language locale packages.

Layout is ``locales/<lang>/<group>.py``, where *group* is a feature — ``rag``
for grounded answering, ``chat`` for ordinary conversation. One file per
feature per language, so changing how retrieval is framed never means editing
the file that also holds the plain-chat prompt.

    parser = TemplateParser(lang="ar")
    parser.get("rag", "system_prompt")
    parser.get("rag", "footer_prompt", {"question": "..."})
"""

import importlib

from utils import get_logger

from .locales import SUPPORTED_LANGS

logger = get_logger(__name__)


class TemplateParser:
    """Resolves (lang, group, key) to a formatted prompt string."""

    def __init__(self, lang: str | None = None, default_lang: str = "en") -> None:

        if default_lang not in SUPPORTED_LANGS:
            raise ValueError(
                f"default_lang {default_lang!r} is not one of {list(SUPPORTED_LANGS)}"
            )

        self.default_lang = default_lang

        self.lang = self._resolve(lang)

        # Imported modules are cached here rather than re-imported per message.
        # importlib caches too, but this keeps the hot path a dict lookup.
        self._groups: dict[tuple[str, str], object] = {}

    def _resolve(self, lang: str | None) -> str:
        """The language to use, falling back when it isn't supported."""
        if lang is None:
            return self.default_lang

        normalized = str(lang).strip().lower()

        if normalized not in SUPPORTED_LANGS:
            # Not an error: an unknown language should still get an answer, in
            # the default language, rather than a 500.
            logger.warning(
                "Unsupported language %r; falling back to %r", lang, self.default_lang
            )
            return self.default_lang

        return normalized

    def _module(self, group: str, lang: str):
        """Import (and cache) one locale group module."""
        cached = self._groups.get((lang, group))
        if cached is not None:
            return cached

        module = importlib.import_module(f"templates.locales.{lang}.{group}")

        self._groups[(lang, group)] = module

        return module

    def get(self, group: str, key: str, vars: dict | None = None) -> str:
        """Return the prompt at *group*.*key*, formatted with *vars*.

        Falls back to the default language only when the whole group is missing
        for this language. A group that exists but lacks *key* raises: the
        locales are meant to stay in step, and silently serving an English
        prompt inside an Arabic conversation is worse than a loud failure.
        """
        try:
            module = self._module(group, self.lang)

        except ModuleNotFoundError:
            if self.lang == self.default_lang:
                raise

            logger.warning(
                "No %r prompts for language %r; using %r",
                group,
                self.lang,
                self.default_lang,
            )
            module = self._module(group, self.default_lang)

        try:
            template = getattr(module, key)

        except AttributeError as exc:
            raise AttributeError(
                f"Prompt {key!r} is missing from templates/locales/{self.lang}/{group}.py"
            ) from exc

        if not vars:
            return template

        return template.format(**vars)
