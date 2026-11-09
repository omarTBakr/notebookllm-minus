"""What a guardrail decides, and the shape the guard model must answer in."""

from dataclasses import asdict, dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class Verdict:
    """One guardrail decision.

    ``safe`` is None when no decision could be made (the worker was down, the
    check timed out, the guard model's answer did not parse or skipped this
    item). The caller turns that into a real decision with GUARDRAIL_FAIL_MODE;
    see :meth:`ModelGuardrail.resolve`.
    """

    safe: bool | None
    category: str = "safe"
    reason: str = ""
    #: True when this verdict came from GUARDRAIL_FAIL_MODE, not from a model.
    by_fail_mode: bool = False

    @classmethod
    def undecided(cls, reason: str) -> "Verdict":
        return cls(safe=None, category="undecided", reason=reason[:300])

    @classmethod
    def from_dict(cls, data: dict | None) -> "Verdict":
        if not isinstance(data, dict):
            return cls.undecided(f"malformed verdict: {data!r}"[:300])
        safe = data.get("safe")
        return cls(
            safe=safe if isinstance(safe, bool) else None,
            category=str(data.get("category") or ("safe" if safe else "undecided")),
            reason=str(data.get("reason") or "")[:300],
            by_fail_mode=bool(data.get("by_fail_mode", False)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class GuardItemVerdict(BaseModel):
    """The guard model's verdict on one item of the batch."""

    # Batch-local: the "### Item n" number the item was shown under, from 0.
    # Never a number the item's own text mentions.
    idx: int = Field(..., ge=0)
    safe: bool
    category: str = Field(..., min_length=1)
    reason: str


class GuardVerdicts(BaseModel):
    """Every verdict for one batch. Required, with no default: an empty answer
    must fail validation and be retried, not silently pass every item."""

    verdicts: list[GuardItemVerdict] = Field(..., min_length=1)

    model_config = {
        "json_schema_extra": {
            "example": {
                "verdicts": [
                    {"idx": 0, "safe": True, "category": "safe", "reason": "EXAMPLE ONLY - an ordinary question"},
                    {"idx": 1, "safe": False, "category": "harmful", "reason": "EXAMPLE ONLY - asks for a weapon"},
                ]
            }
        }
    }
