import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enums import DISTANCE_METHOD_TO_PGVECTOR, DistanceMethod, IndexType
from exceptions import DbError
from utils import get_logger

from ..interfaces.vector_repository import VectorRepository
from .base_repository import PostgresBaseRepository

# The identifier quoter for Postgres. Table names below are built from
# collection names, so they go through this rather than into an f-string raw.
_PREPARER = postgresql.dialect().identifier_preparer


class PostgresVectorRepository(PostgresBaseRepository, VectorRepository):
    """PostgreSQL implementation of VectorRepository using pgvector.

    Each 'collection' is implemented as a separate table to allow independent
    vector indices and optimized distance searching.

    This is the one part of the backend Alembic does not own, and cannot: the
    tables are created per chat at runtime, and the vector width is not known
    until an embedding model is chosen. A single shared table would mean one
    fixed width for every chat, or a column no HNSW index can cover. So the DDL
    stays here, written by hand — but executed through the same engine and the
    same session factory as everything else.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        distance_method: str = "cosine",
        index_type: str = "hnsw",
    ) -> None:
        super().__init__(session_factory)
        self.distance_method = DistanceMethod(distance_method)
        self.index_type = IndexType(index_type)
        self.logger = get_logger(type(self).__module__)

    def _table_name(self, collection_name: str) -> str:
        """Sanitize collection name for use as a table name."""
        # Assume collection_name is safe (it's usually a project ID).
        # We prefix it to avoid colliding with other tables.
        clean_name = "".join(c if c.isalnum() else "_" for c in collection_name)
        return f"vec_{clean_name}"

    def _quoted_table(self, collection_name: str) -> str:
        """The table name as it may be interpolated into a DDL string.

        Sanitising is not quoting: it stops SQL from being injected but still
        leaves an identifier the server has to parse. Belt and braces.
        """
        return _PREPARER.quote(self._table_name(collection_name))

    async def collection_exists(self, collection_name: str) -> bool:
        try:
            async with self.session_factory() as db:
                result = await db.scalar(
                    text("SELECT EXISTS (SELECT FROM information_schema.tables " "WHERE table_name = :name)"),
                    {"name": self._table_name(collection_name)},
                )
                return bool(result)
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to check if collection exists: {exc}") from exc

    async def list_collections(self) -> list[str]:
        try:
            async with self.session_factory() as db:
                result = await db.execute(
                    text("SELECT table_name FROM information_schema.tables " "WHERE table_name LIKE 'vec\\_%'")
                )
                # Strip the 'vec_' prefix. Note this does not fully invert
                # _table_name: the punctuation it replaced with '_' cannot be
                # put back, so a name with a dash comes out with an underscore.
                # The only callers (the /nlp health checks) count these rather
                # than look them up, so it has never mattered.
                return [name[4:] for (name,) in result.all()]
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to list collections: {exc}") from exc

    async def get_collection_info(self, collection_name: str) -> dict:
        if not await self.collection_exists(collection_name):
            return {"status": "missing"}

        table = self._quoted_table(collection_name)

        try:
            async with self.session_factory() as db:
                count = await db.scalar(text(f"SELECT COUNT(*) FROM {table}"))
                return {
                    "status": "green",
                    "points_count": count,
                    "config": {"params": {"distance": self.distance_method.value}},
                }
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to get collection info: {exc}") from exc

    # pgvector's ceiling for an HNSW or IVFFlat index over a `vector` column.
    # Storage allows more; only the index is capped.
    MAX_INDEXABLE_DIMENSIONS = 2000

    # The same index over `halfvec` — pgvector's 2-byte float — reaches twice
    # as far, which is the documented way past the limit above.
    MAX_HALFVEC_INDEXABLE_DIMENSIONS = 4000

    def _index_expression(self, embedding_size: int) -> tuple[str, str, str]:
        """How to index and query a column of this width.

        Returns the expression to index, the operator class for it, and the
        cast a query's own vector needs. For anything pgvector can index
        directly these are just the column and its `vector_*` opclass.

        Past 2000 dimensions the column stays `vector` — full precision on
        disk, and every stored value is untouched — while the *index* is built
        over a `halfvec` cast of it. Measured on this project's own 2048-dim
        embeddings, replicated to 24k rows: an exact scan took 120-185 ms and
        the halfvec index 0.85-0.98 ms, for 30 MB of index. Precision costs
        nothing detectable — over 20 real queries the top 10 came back
        identical, in the same order.

        The catch, and the reason all three values are returned together: a
        Postgres expression index is only used when the query's ORDER BY
        matches the expression *exactly*. Index on `embedding::halfvec(2048)`
        and query on plain `embedding` and you get a fully built index that is
        silently never consulted, with no error to say so. Both sides are
        derived here so they cannot drift apart.
        """
        _, opclass = DISTANCE_METHOD_TO_PGVECTOR[self.distance_method]

        # halfvec earns its precision loss only where it buys an index, so it
        # applies to a band rather than to everything above 2000. Past 4000
        # nothing is indexable at all and every search is an exact scan either
        # way — casting there would spend the precision and get nothing back.
        if not (self.MAX_INDEXABLE_DIMENSIONS < embedding_size <= self.MAX_HALFVEC_INDEXABLE_DIMENSIONS):
            return "embedding", opclass, "::vector"

        # pgvector names the halfvec operator classes by substituting the type
        # name and nothing else. Checked against pg_opclass on pgvector 0.8.6,
        # for all three distance methods and both index types: halfvec_cosine_ops,
        # halfvec_ip_ops and halfvec_l2_ops all exist for hnsw *and* ivfflat.
        return (
            f"(embedding::halfvec({embedding_size}))",
            opclass.replace("vector_", "halfvec_", 1),
            f"::halfvec({embedding_size})",
        )

    async def create_collection(self, collection_name: str, embedding_size: int, reset: bool = False) -> bool:
        # Interpolated into the DDL below, so it has to be a number and not
        # something that merely stringifies into one.
        if not isinstance(embedding_size, int) or isinstance(embedding_size, bool):
            raise DbError(f"embedding_size must be an int, got {embedding_size!r}")
        if embedding_size < 1:
            raise DbError(f"embedding_size must be positive, got {embedding_size}")

        table = self._quoted_table(collection_name)

        try:
            if await self.collection_exists(collection_name):
                if not reset:
                    return False
                async with self.session_factory.begin() as db:
                    await db.execute(text(f"DROP TABLE IF EXISTS {table}"))

            async with self.session_factory.begin() as db:
                # No index here — see create_index(). Building HNSW or
                # IVFFlat incrementally as insert_many() streams rows in is
                # far slower than inserting first and indexing once, and
                # IVFFlat's cluster count is only meaningful once there is
                # data to cluster.
                await db.execute(text(f"""
                        CREATE TABLE {table} (
                            id VARCHAR(200) PRIMARY KEY,
                            embedding VECTOR({embedding_size}),
                            text TEXT,
                            metadata JSONB
                        )
                        """))

            return True
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create collection {collection_name}: {exc}") from exc

    async def create_index(
        self,
        collection_name: str,
        embedding_size: int,
        index_type: IndexType | None = None,
        reset: bool = False,
    ) -> bool:
        # Past 4000 dimensions not even halfvec can be indexed. The column is
        # still fine and search still works — it just does an exact scan
        # instead of an approximate one. Refusing outright would mean a wide
        # embedding model simply could not be used.
        if embedding_size > self.MAX_HALFVEC_INDEXABLE_DIMENSIONS:
            self.logger.warning(
                "Collection %r has %d dimensions, above pgvector's halfvec "
                "indexing limit of %d — left without an index, so searches "
                "will be exact scans. Use a narrower embedding model if that "
                "gets slow.",
                collection_name,
                embedding_size,
                self.MAX_HALFVEC_INDEXABLE_DIMENSIONS,
            )
            return False

        chosen = IndexType(index_type) if index_type is not None else self.index_type
        table = self._quoted_table(collection_name)
        index = _PREPARER.quote(f"idx_{self._table_name(collection_name)}_embedding")
        column, opclass, _ = self._index_expression(embedding_size)

        if embedding_size > self.MAX_INDEXABLE_DIMENSIONS:
            self.logger.info(
                "Collection %r has %d dimensions, past pgvector's %d limit for "
                "a vector index — indexing a halfvec cast of it instead. "
                "search_by_vector casts to match, or the index is not used.",
                collection_name,
                embedding_size,
                self.MAX_INDEXABLE_DIMENSIONS,
            )

        try:
            async with self.session_factory.begin() as db:
                if reset:
                    await db.execute(text(f"DROP INDEX IF EXISTS {index}"))

                if chosen is IndexType.HNSW:
                    await db.execute(
                        text(f"CREATE INDEX IF NOT EXISTS {index} ON {table} " f"USING hnsw ({column} {opclass})")
                    )
                else:
                    # IVFFlat's cluster count should scale with row count —
                    # pgvector's own rule of thumb: rows/1000 up to 1M rows,
                    # sqrt(rows) beyond that. An empty table still gets a
                    # valid (if not yet useful) index rather than failing.
                    count = await db.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0
                    lists = max(1, int(count**0.5) if count > 1_000_000 else count // 1000)
                    await db.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS {index} ON {table} "
                            f"USING ivfflat ({column} {opclass}) WITH (lists = {lists})"
                        )
                    )
            return True
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to create index for {collection_name}: {exc}") from exc

    async def delete_collection(self, collection_name: str) -> bool:
        existed = await self.collection_exists(collection_name)
        table = self._quoted_table(collection_name)

        try:
            async with self.session_factory.begin() as db:
                await db.execute(text(f"DROP TABLE IF EXISTS {table}"))
            return existed
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete collection {collection_name}: {exc}") from exc

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        """pgvector's text input format. Cast to ::vector on the way in."""
        return "[" + ",".join(str(v) for v in vector) + "]"

    async def insert_many(
        self,
        collection_name: str,
        texts: list[str],
        vectors: list[list[float]],
        metadata: list[dict] | None = None,
        record_ids: list[str] | None = None,
        batch_size: int = 64,
    ) -> bool:
        if not texts:
            return True

        table = self._quoted_table(collection_name)

        rows = []
        for i in range(len(texts)):
            r_id = record_ids[i] if record_ids and record_ids[i] else str(uuid.uuid4())
            rows.append(
                {
                    "id": r_id,
                    "embedding": self._vector_literal(vectors[i]),
                    "text": texts[i],
                    "metadata": metadata[i] if metadata else {},
                }
            )

        statement = text(f"""
            INSERT INTO {table} (id, embedding, text, metadata)
            VALUES (:id, (:embedding)::vector, :text, :metadata)
            ON CONFLICT (id) DO UPDATE
            SET embedding = EXCLUDED.embedding,
                text = EXCLUDED.text,
                metadata = EXCLUDED.metadata
            """).bindparams(bindparam("metadata", type_=JSONB))

        try:
            async with self.session_factory.begin() as db:
                # Honour batch_size, which the interface documents and the old
                # implementation ignored: one executemany for a whole document
                # is a single very large round trip.
                for start in range(0, len(rows), batch_size):
                    await db.execute(statement, rows[start : start + batch_size])
            return True
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to insert into {collection_name}: {exc}") from exc

    async def delete_by_metadata(self, collection_name: str, key: str, value: str) -> int:
        if not await self.collection_exists(collection_name):
            return 0

        table = self._quoted_table(collection_name)

        try:
            async with self.session_factory.begin() as db:
                # ->> operator extracts jsonb field as text
                result = await db.execute(
                    text(f"DELETE FROM {table} WHERE metadata->>:key = :value"),
                    {"key": key, "value": value},
                )
                return result.rowcount
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to delete by metadata in {collection_name}: {exc}") from exc

    def _to_similarity(self, distance: float) -> float:
        """Turn a pgvector distance into a higher-is-better similarity.

        cosine: pgvector gives ``1 - cos_sim`` in [0, 2], so ``1 - d`` recovers
        the cosine in [-1, 1]. dot: pgvector negates the inner product, so
        ``-d`` is the product back. euclid: an unbounded distance with no
        natural similarity, so negate it — the ordering is preserved and larger
        is still better, which is all a comparison or a floor needs.
        """
        if self.distance_method == DistanceMethod.COSINE:
            return 1.0 - distance
        return -distance

    async def search_by_vector(
        self,
        collection_name: str,
        vector: list[float],
        limit: int = 5,
        asset_ids: list[str] | None = None,
    ) -> list[dict]:
        if not await self.collection_exists(collection_name):
            return []

        table = self._quoted_table(collection_name)
        operator, _ = DISTANCE_METHOD_TO_PGVECTOR[self.distance_method]

        # The width comes from the query vector itself, which is the same
        # model that produced the column — no lookup, and nothing to fall out
        # of step. Both sides of the comparison must carry the *same* cast as
        # the index was built with: an expression index Postgres cannot match
        # against the ORDER BY is simply not used, silently. See
        # _index_expression.
        column, _, cast = self._index_expression(len(vector))

        params = {"vector": self._vector_literal(vector), "limit": limit}
        where = ""

        if asset_ids:
            where = " WHERE metadata->>'asset_id' = ANY(:asset_ids)"
            params["asset_ids"] = list(asset_ids)

        query = text(f"""
            SELECT id, text, metadata,
                   ({column} {operator} (:vector){cast}) AS distance
            FROM {table}{where}
            ORDER BY {column} {operator} (:vector){cast}
            LIMIT :limit
            """)

        try:
            async with self.session_factory() as db:
                result = await db.execute(query, params)

                # .mappings() rather than attribute access: one of the columns
                # is called `metadata`, which is not a name to reach for on a
                # SQLAlchemy object.
                return [
                    {
                        "id": row["id"],
                        # A *similarity*, not the raw distance pgvector
                        # returns, so this field means the same thing on both
                        # backends: higher is better, 1.0 is a perfect cosine
                        # match. Qdrant already reports it that way; returning
                        # a distance here made 0.0 mean "perfect" on one
                        # backend and "unrelated" on the other, which any
                        # threshold would then get exactly backwards.
                        "score": self._to_similarity(float(row["distance"])),
                        "text": row["text"],
                        "metadata": row["metadata"] or {},
                    }
                    for row in result.mappings().all()
                ]
        except SQLAlchemyError as exc:
            raise DbError(f"Failed to search in {collection_name}: {exc}") from exc
