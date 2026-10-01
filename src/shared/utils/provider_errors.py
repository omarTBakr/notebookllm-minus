"""Reading a vendor's refusal — did the call get through, and if not, why.

Three questions asked of an exception's text, and nothing else. There is no
structured error to read: the vendor SDKs each raise their own class, wrap
the HTTP status differently, and the only thing reliably common to all of them
is the sentence the vendor wrote. So this matches on that, and the matching is
kept here rather than inside the controller that probes, because "was this a
rate limit" is not a fact about the catalogue.
"""

# Matched against the provider's error text, longest-lived cause first. The
# vendor sentences are long and change wording; the picker needs a phrase
# short enough to sit in a row.
_UNAVAILABLE_REASONS = (
    ("credit balance", "No API credit"),
    ("no longer available", "Retired by the vendor"),
    ("quota", "Quota exceeded"),
    ("workspace", "Workspace id required"),
    ("api key", "API key rejected"),
    ("unauthenticated", "API key rejected"),
    ("permission", "Key not permitted"),
    ("not found", "Not available to this key"),
)


def rate_limited(exc: Exception) -> bool:
    """Whether the vendor refused because the account is over its rate."""
    text = str(exc).lower()

    return "429" in text or "too many requests" in text or "rate limit" in text


def reached_generation(exc: Exception) -> bool:
    """Whether this failure happened *after* the model accepted the call.

    A model that ran out of output budget got further than any check here
    cares about, so the truncation counts as a pass. Both halves are
    required: "finish_reason" pins it to a response the vendor actually
    produced, so a 400 complaining about the max_tokens *field* — a request
    that never reached the model — is not mistaken for one.
    """
    text = str(exc).lower()

    return "finish_reason" in text and "max_tokens" in text


def unavailable_reason(exc: Exception) -> str:
    """A row-sized phrase for why a vendor refused the probe."""
    text = str(exc).lower()

    for needle, reason in _UNAVAILABLE_REASONS:
        if needle in text:
            return reason

    # Nothing recognised. Better to show the vendor's own first sentence,
    # trimmed, than to invent a category for it.
    first = str(exc).split(".")[0].strip()

    return (first[:77] + "...") if len(first) > 80 else (first or "Unavailable")
