import json
from functools import lru_cache
from typing import TypeVar, Type, get_args

import asyncpg
from pydantic import BaseModel
from bson.objectid import ObjectId

T = TypeVar("T", bound=BaseModel)


class PostgresBaseRepository:
    """Base repository for Postgres that provides common utility methods."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    def _generate_id(self) -> str:
        """Generate a 24-char hex string to mimic MongoDB ObjectId behavior."""
        return str(ObjectId())

    def _record_to_model(self, record: asyncpg.Record | None, model_class: Type[T]) -> T | None:
        """Convert an asyncpg Record to a Pydantic model."""
        if not record:
            return None

        # asyncpg.Record is tuple-like; a dict is what pydantic wants.
        data = dict(record)

        # Built as a new dict rather than mutated in place: the previous
        # version popped a key while iterating the same dict, which raises
        # "dictionary keys changed during iteration" whenever the removal and
        # the insertion happen to make CPython resize.
        decoded = {}

        for key, value in data.items():
            # asyncpg hands JSONB back as a string unless a codec is
            # registered, and a list column would then fail validation.
            if isinstance(value, str) and value[:1] in ("{", "["):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass

            # The models alias the primary key as `_id`.
            decoded["_id" if key == "id" else key] = value

        # The schemas are shared with the Mongo backend, so any id field is
        # typed as a real ObjectId. Postgres stores the 24-hex string form and
        # pydantic will not take a str for such a field, so every read failed
        # validation. Driven off the model rather than a hardcoded list of
        # names: `_id` is one, DataChunk.project_id is another, and the next
        # one should not need a code change here.
        for name in self._objectid_fields(model_class):
            value = decoded.get(name)
            if isinstance(value, str) and ObjectId.is_valid(value):
                decoded[name] = ObjectId(value)

        return model_class.model_validate(decoded)

    @staticmethod
    @lru_cache(maxsize=None)
    def _objectid_fields(model_class: Type[T]) -> tuple[str, ...]:
        """Field names on *model_class* that hold an ObjectId, by alias."""
        names = []

        for name, field in model_class.model_fields.items():
            annotation = field.annotation
            candidates = (annotation,) + get_args(annotation)
            if any(c is ObjectId for c in candidates):
                names.append(field.alias or name)

        return tuple(names)

    def _records_to_models(self, records: list[asyncpg.Record], model_class: Type[T]) -> list[T]:
        return [self._record_to_model(r, model_class) for r in records if r]
