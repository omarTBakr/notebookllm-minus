"""Loads prompt strings out of the per-language locale packages.

Layout is ``locales/<lang>/<group>.py``, where *group* is a feature — ``rag``
for grounded answering, ``chat`` for ordinary conversation. One file per
feature per language, so changing how retrieval is framed never means editing
the file that also holds the plain-chat prompt.

    parser = TemplateParser(lang="ar")
    parser.get("rag", "system_prompt")
    parser.get("rag", "footer_prompt", {"question": "..."})

**Versioning.** With Langfuse on (``LANGFUSE_ENABLED`` plus keys, and
``LANGFUSE_PROMPTS_ENABLED``), each prompt is fetched from Langfuse as
``<lang>/<group>.<key>`` at ``LANGFUSE_PROMPT_LABEL``, so a new version goes
live by moving that label in the UI, with no deploy. The locale files stay the
source of truth (``scripts/langfuse_seed_prompts.py`` pushes them) and the
fallback: Langfuse off, unreachable, or missing the prompt all serve the local
text. Placeholders stay ``{var}`` and are filled with ``str.format`` either way.
"""

import importlib
import pkgutil
from collections.abc import Iterator

from shared.utils import get_logger, get_settings, tracing

from .locales import SUPPORTED_LANGS

logger = get_logger(__name__)

# Bounds the one blocking fetch a prompt costs on a cold cache. After that the
# SDK serves from memory and refreshes in the background once the TTL passes.
_FETCH_TIMEOUT_S = 2


def langfuse_name(lang: str, group: str, key: str) -> str:
    """The name a locale prompt is stored under in Langfuse."""
    return f"{lang}/{group}.{key}"


def iter_local_prompts() -> Iterator[tuple[str, str, str, str]]:
    """Every (lang, group, key, text) in the locale packages.

    What gets seeded into Langfuse: every public module-level string.
    """
    for lang in SUPPORTED_LANGS:
        package = importlib.import_module(f"application.prompts.locales.{lang}")
        for info in pkgutil.iter_modules(package.__path__):
            module = importlib.import_module(f"{package.__name__}.{info.name}")
            for key, value in vars(module).items():
                if not key.startswith("_") and isinstance(value, str):
                    yield lang, info.name, key, value


class TemplateParser:
    """Resolves (lang, group, key) to a formatted prompt string."""

    def __init__(self, lang: str | None = None, default_lang: str = "en") -> None:

        if default_lang not in SUPPORTED_LANGS:
            raise ValueError(f"default_lang {default_lang!r} is not one of {list(SUPPORTED_LANGS)}")

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
            logger.warning("Unsupported language %r; falling back to %r", lang, self.default_lang)
            return self.default_lang

        return normalized

    def _module(self, group: str, lang: str):
        """Import (and cache) one locale group module."""
        cached = self._groups.get((lang, group))
        if cached is not None:
            return cached

        module = importlib.import_module(f"application.prompts.locales.{lang}.{group}")

        self._groups[(lang, group)] = module

        return module

    def get(self, group: str, key: str, vars: dict | None = None) -> str:
        """Return the prompt at *group*.*key*, formatted with *vars*."""
        return self.get_with_ref(group, key, vars)[0]

    def get_with_ref(self, group: str, key: str, vars: dict | None = None) -> tuple[str, object | None]:
        """The formatted prompt, plus the Langfuse prompt it came from.

        The second item is None when the text is the local fallback. Pass it to
        ``tracing.linked_prompt`` around the model call so the trace records
        which prompt version produced the answer.
        """
        local, lang = self._local(group, key)

        template, ref = self._versioned(lang, group, key, local)

        if not vars:
            return template, ref

        try:
            return template.format(**vars), ref
        except (KeyError, IndexError, ValueError) as exc:
            if ref is None:
                raise
            # A version edited in the UI with a broken placeholder must not
            # take chat down: log it and serve the local text instead.
            logger.warning(
                "Langfuse prompt %s v%s does not format (%s); using the local text",
                langfuse_name(lang, group, key),
                getattr(ref, "version", "?"),
                exc,
            )
            return local.format(**vars), None

    def _versioned(self, lang: str, group: str, key: str, local: str) -> tuple[str, object | None]:
        """The Langfuse version of a prompt, or the local text."""
        settings = get_settings()
        client = tracing.get_langfuse() if settings.LANGFUSE_PROMPTS_ENABLED else None

        if client is None:
            return local, None

        try:
            prompt = client.get_prompt(
                langfuse_name(lang, group, key),
                label=settings.LANGFUSE_PROMPT_LABEL,
                cache_ttl_seconds=settings.LANGFUSE_PROMPT_CACHE_TTL,
                fallback=local,
                max_retries=0,
                fetch_timeout_seconds=_FETCH_TIMEOUT_S,
            )
        except Exception as exc:
            logger.debug(
                "Prompt %s not served by Langfuse: %s",
                langfuse_name(lang, group, key),
                exc,
            )
            return local, None

        if getattr(prompt, "is_fallback", False) or not isinstance(prompt.prompt, str):
            return local, None

        return prompt.prompt, prompt

    def _local(self, group: str, key: str) -> tuple[str, str]:
        """The local template at *group*.*key*, and the language it came from.

        Falls back to the default language only when the whole group is missing
        for this language. A group that exists but lacks *key* raises: the
        locales are meant to stay in step, and silently serving an English
        prompt inside an Arabic conversation is worse than a loud failure.
        """
        try:
            module = self._module(group, self.lang)
            lang = self.lang

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
            lang = self.default_lang

        try:
            template = getattr(module, key)

        except AttributeError as exc:
            raise AttributeError(f"Prompt {key!r} is missing from templates/locales/{self.lang}/{group}.py") from exc

        return template, lang


def warm_prompts() -> int:
    """Fetch every prompt once, so no chat pays for a cold cache.

    Blocking; the API runs it in a thread at startup. Returns how many prompts
    Langfuse served (0 when it is off).
    """
    settings = get_settings()
    if not (settings.LANGFUSE_PROMPTS_ENABLED and tracing.get_langfuse()):
        return 0

    served = 0
    parsers = {lang: TemplateParser(lang=lang) for lang in SUPPORTED_LANGS}
    for lang, group, key, text in iter_local_prompts():
        _, ref = parsers[lang]._versioned(lang, group, key, text)
        served += ref is not None
    return served
