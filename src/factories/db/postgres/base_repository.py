"""Shared Postgres plumbing: the ORM schema, and rows back into models.

The tables are declared once, here, as SQLAlchemy classes. Alembic reads
``Base.metadata`` to generate migrations and the repositories build their
queries against the same classes, so a column that does not exist is an
AttributeError at import rather than a 503 on the first request. That is the
whole point: the previous hand-written SQL drifted from both the DDL and the
pydantic models without anything noticing.

Two naming rules keep this readable:

* ORM classes carry a ``Row`` suffix, because the pydantic model of the same
  name (``User``, ``Chat``, ...) is imported into every repository alongside it.
* Instances of them are called ``row``, never ``chat``/``user``. The static
  check in test/db/test_postgres_field_mapping.py greps for ``chat.<attr>`` to
  catch fields read off a model that does not have them; naming an ORM instance
  ``chat`` would feed it false positives.
"""

import json
from datetime import datetime
from functools import lru_cache
from typing import Any, TypeVar, Type, get_args

from bson.objectid import ObjectId
from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    inspect as sa_inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

T = TypeVar("T", bound=BaseModel)


class Base(DeclarativeBase):
    """Declarative base. ``Base.metadata`` is what Alembic migrates."""


# Row ids are 24-hex strings holding a str(ObjectId()) — see _generate_id. The
# pydantic models are shared verbatim with the Mongo backend, where `id` is a
# real bson.ObjectId, so Postgres stores that same shape and converts back on
# read. Native UUIDs would mean changing models that Mongo also uses.
_OID = String(24)
_BIZ_ID = String(200)


def _utcnow_column():  # -> MappedColumn; annotated on the class attribute instead
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    user_id: Mapped[str] = mapped_column(_BIZ_ID, unique=True, nullable=False)
    # User.label is `str` with a default, not Optional — a NULL here would fail
    # pydantic validation on every read. Same reasoning for every NOT NULL
    # DEFAULT below: the column has to be at least as strict as the model.
    label: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")

    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column()


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    session_id: Mapped[str] = mapped_column(_BIZ_ID, unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(_BIZ_ID, nullable=False)
    # Missing from the old DDL entirely, so every read resurrected the model's
    # default and a renamed session forgot its name on the next request.
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default="New session"
    )

    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column()


class ChatRow(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    chat_id: Mapped[str] = mapped_column(_BIZ_ID, unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(_BIZ_ID, nullable=False)
    user_id: Mapped[str] = mapped_column(_BIZ_ID, nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    lang: Mapped[str] = mapped_column(String(8), nullable=False, server_default="en")

    # None means "whatever .env says", so these stay genuinely nullable.
    generation_model: Mapped[str | None] = mapped_column(String(200))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    chunk_size: Mapped[int | None] = mapped_column(Integer)
    overlap_size: Mapped[int | None] = mapped_column(Integer)

    web_search: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    excluded_assets: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    has_documents: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column()


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    message_id: Mapped[str] = mapped_column(_BIZ_ID, unique=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(_BIZ_ID, nullable=False)

    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = _utcnow_column()


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    project_id: Mapped[str] = mapped_column(_BIZ_ID, unique=True, nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # Lists of 24-hex row ids, as strings — ObjectId is not JSON-serialisable.
    chunks_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    assets_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column()


class AssetRow(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    asset_id: Mapped[str] = mapped_column(_BIZ_ID, unique=True, nullable=False)
    # The *business* project_id, unlike ChunkRow.project_id below.
    project_id: Mapped[str] = mapped_column(_BIZ_ID, nullable=False)

    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    file_bytes: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, server_default=text(r"'\x'::bytea")
    )
    # sha256 of file_bytes, hex. Unique per project — see migration 0003.
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )

    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column()


class ChunkRow(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(_OID, primary_key=True)
    # The project's *row* id, not its business project_id — DataChunk.project_id
    # is an ObjectId. AssetRow.project_id holds the other one. That
    # inconsistency is why none of these columns carry a foreign key: adding one
    # would have to pick a side, and ON DELETE CASCADE would quietly change what
    # delete_project() removes. Left as a follow-up.
    project_id: Mapped[str] = mapped_column(_OID, nullable=False)
    asset_id: Mapped[str | None] = mapped_column(_BIZ_ID)

    chunk_order: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _utcnow_column()
    updated_at: Mapped[datetime] = _utcnow_column()


# Declared out here rather than in __table_args__ so they can reference the
# mapped columns directly. Plain ascending, even where the query sorts DESC:
# Postgres scans a btree backwards at the same cost, and a DESC index reflects
# back differently enough to show up as spurious autogenerate churn.
Index("idx_sessions_user_id", SessionRow.user_id, SessionRow.created_at)
Index("idx_chats_session_id", ChatRow.session_id, ChatRow.created_at)
Index("idx_chats_user_id", ChatRow.user_id, ChatRow.created_at)
Index("idx_messages_chat_id", MessageRow.chat_id, MessageRow.created_at)
Index("idx_assets_project_id", AssetRow.project_id, AssetRow.created_at)
Index("idx_chunks_project_id", ChunkRow.project_id, ChunkRow.created_at)
Index("idx_chunks_project_asset", ChunkRow.project_id, ChunkRow.asset_id)
Index(
    "idx_chunks_project_asset_order",
    ChunkRow.project_id,
    ChunkRow.asset_id,
    ChunkRow.chunk_order,
)


def _as_objectid(value):
    """A 24-hex string becomes an ObjectId. Anything else is left alone —
    including a string that is not a valid one, so pydantic reports it."""
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return value


def _as_objectids(value):
    """Same, but also reaching one level into a list.

    Project.chunks_ids and assets_ids are `list[ObjectId]` stored as JSONB
    arrays of hex strings, so the scalar conversion alone left every project
    that had ever ingested a document failing validation on read.
    """
    if isinstance(value, list):
        return [_as_objectid(item) for item in value]
    return _as_objectid(value)


class PostgresBaseRepository:
    """Base repository for Postgres that provides common utility methods."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    def _generate_id(self) -> str:
        """Generate a 24-char hex string to mimic MongoDB ObjectId behavior."""
        return str(ObjectId())

    @classmethod
    def _scrub(cls, value: Any) -> Any:
        """Strip NUL bytes from anything on its way into a row.

        PostgreSQL refuses \\x00 in both text and jsonb — it stores strings
        NUL-terminated, so no encoding or client setting makes it storable.
        Mongo accepts them, so this constraint belongs to this backend alone
        and none of the callers know about it.

        The ingest path already strips them at extraction, which is where PDF
        text acquires them. This is the second line: a batch INSERT fails
        *whole*, so one NUL arriving from anywhere else — a filename, a note
        title, metadata assembled after extraction — would cost every row in
        the batch. Cheap on clean input: str.replace returns the same object
        when there is nothing to replace.
        """
        if isinstance(value, str):
            return value.replace("\x00", "")
        if isinstance(value, dict):
            return {cls._scrub(k): cls._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._scrub(v) for v in value]
        return value

    def _record_to_model(self, record: Any | None, model_class: Type[T]) -> T | None:
        """Convert a database row to a Pydantic model.

        Takes an ORM instance, or any mapping — SQLAlchemy ``RowMapping``, or a
        plain dict, which is what the tests use so they need no server.
        """
        if record is None:
            return None

        if isinstance(record, Base):
            mapper = sa_inspect(type(record)).mapper
            data = {attr.key: getattr(record, attr.key) for attr in mapper.column_attrs}
        else:
            if not record:
                return None
            data = dict(record)

        # Built as a new dict rather than mutated in place: the previous
        # version popped a key while iterating the same dict, which raises
        # "dictionary keys changed during iteration" whenever the removal and
        # the insertion happen to make CPython resize.
        decoded = {}

        for key, value in data.items():
            # The JSONB type hands back real lists and dicts, so this is a
            # no-op on live rows. It stays for the raw-string case (a dict
            # handed straight to this method, or a text column holding JSON).
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
            if name in decoded:
                decoded[name] = _as_objectids(decoded[name])

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

    def _records_to_models(self, records: list, model_class: Type[T]) -> list[T]:
        return [self._record_to_model(r, model_class) for r in records if r is not None]
