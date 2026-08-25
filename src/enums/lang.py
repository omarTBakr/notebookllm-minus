from enum import Enum


class Language(str, Enum):
    """Locales the prompt templates are written in.

    The single source of truth for what `templates/locales/` contains: adding a
    locale means adding a member here and a directory there. ``SUPPORTED_LANGS``
    is derived from this, so the two cannot drift.
    """

    EN = "en"
    AR = "ar"  # right-to-left; the UI flips direction on this
