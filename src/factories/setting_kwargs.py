"""Turning a {keyword: settings field} table into constructor keyword arguments.

Shared by both factories, in the shape of ``cohere_support``: the chatting and
embedding sides ask the same question of their own table — *which of these
fields is actually set, and what does each one fill in?* — and one answer
means adding a knob to a provider stays a line in ProviderMappings plus a
field on Settings, with no factory learning about a vendor.
"""


def setting_kwargs(settings, fields: dict[str, str]) -> dict:
    """``{keyword: value}`` for the *fields* that have a value to give.

    A field that is unset or blank is left out rather than passed as ``None``,
    so the provider's own signature default stays in charge — which is what
    makes an optional endpoint (OPENAI_API_BASE_URL) and a defaulted one
    (NVIDIA_API_BASE_URL) work through the same table.
    """
    resolved = {}

    for keyword, field in fields.items():
        value = getattr(settings, field)

        if value not in (None, ""):
            resolved[keyword] = value

    return resolved
