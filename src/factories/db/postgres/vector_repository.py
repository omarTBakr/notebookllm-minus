import json
import uuid

from utils import get_logger

from exceptions import DbError
import asyncpg
from enums import DistanceMethod
from .base_repository import PostgresBaseRepository
from ..interfaces.vector_repository import VectorRepository


class PostgresVectorRepository(PostgresBaseRepository, VectorRepository):
    """PostgreSQL implementation of VectorRepository using pgvector.
    
    Each 'collection' is implemented as a separate table to allow independent
    vector indices and optimized distance searching.
    """

    def __init__(self, pool: asyncpg.Pool, distance_method: str = "cosine") -> None:
        super().__init__(pool)
        self.distance_method = DistanceMethod(distance_method)
        self.logger = get_logger(type(self).__module__)

    def _table_name(self, collection_name: str) -> str:
        """Sanitize collection name for use as a table name."""
        # Assume collection_name is safe (it's usually a project ID).
        # We prefix it to avoid colliding with other tables.
        clean_name = "".join(c if c.isalnum() else "_" for c in collection_name)
        return f"vec_{clean_name}"

    async def collection_exists(self, collection_name: str) -> bool:
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = $1
                    )
                    """,
                    self._table_name(collection_name)
                )
                return bool(result)
        except Exception as exc:
            raise DbError(f"Failed to check if collection exists: {exc}") from exc

    async def list_collections(self) -> list[str]:
        try:
            async with self.pool.acquire() as conn:
                records = await conn.fetch(
                    """
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_name LIKE 'vec_%'
                    """
                )
                # Strip the 'vec_' prefix to get back the collection name
                return [r["table_name"][4:] for r in records]
        except Exception as exc:
            raise DbError(f"Failed to list collections: {exc}") from exc

    async def get_collection_info(self, collection_name: str) -> dict:
        table = self._table_name(collection_name)
        exists = await self.collection_exists(collection_name)
        if not exists:
            return {"status": "missing"}
            
        try:
            async with self.pool.acquire() as conn:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                return {
                    "status": "green",
                    "points_count": count,
                    "config": {
                        "params": {
                            "distance": self.distance_method.value
                        }
                    }
                }
        except Exception as exc:
            raise DbError(f"Failed to get collection info: {exc}") from exc

    # pgvector's ceiling for an HNSW index. Storage allows more.
    MAX_INDEXABLE_DIMENSIONS = 2000

    async def create_collection(
        self, collection_name: str, embedding_size: int, reset: bool = False
    ) -> bool:
        table = self._table_name(collection_name)
        
        try:
            async with self.pool.acquire() as conn:
                if await self.collection_exists(collection_name):
                    if not reset:
                        return False
                    await conn.execute(f"DROP TABLE IF EXISTS {table}")
                
                # Create the table with pgvector type
                await conn.execute(f"""
                    CREATE TABLE {table} (
                        id VARCHAR(200) PRIMARY KEY,
                        embedding VECTOR({embedding_size}),
                        text TEXT,
                        metadata JSONB
                    )
                """)
                
                # Create an HNSW index based on the chosen distance method
                # pgvector operators:
                # cosine: vector_cosine_ops (<=>)
                # dot: vector_ip_ops (<#>)
                # euclid: vector_l2_ops (<->)
                opclass = "vector_cosine_ops"
                if self.distance_method == DistanceMethod.DOT:
                    opclass = "vector_ip_ops"
                elif self.distance_method == DistanceMethod.EUCLID:
                    opclass = "vector_l2_ops"
                    
                # pgvector will not build an HNSW index past this width. The
                # column itself is fine, and search still works — it just does
                # an exact scan instead of an approximate one. Refusing to
                # create the collection at all would mean a 4096-dimension
                # embedding model simply could not be used.
                if embedding_size <= self.MAX_INDEXABLE_DIMENSIONS:
                    await conn.execute(f"""
                        CREATE INDEX idx_{table}_embedding ON {table}
                        USING hnsw (embedding {opclass})
                    """)
                else:
                    self.logger.warning(
                        "Collection %r has %d dimensions, above pgvector's HNSW "
                        "limit of %d — created without an index, so searches "
                        "will be exact scans. Use a narrower embedding model if "
                        "that gets slow.",
                        collection_name,
                        embedding_size,
                        self.MAX_INDEXABLE_DIMENSIONS,
                    )

                return True
        except Exception as exc:
            raise DbError(f"Failed to create collection {collection_name}: {exc}") from exc

    async def delete_collection(self, collection_name: str) -> bool:
        table = self._table_name(collection_name)
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(f"DROP TABLE IF EXISTS {table}")
                return result == "DROP TABLE"
        except Exception as exc:
            raise DbError(f"Failed to delete collection {collection_name}: {exc}") from exc

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
            
        table = self._table_name(collection_name)
        
        # Format the data for asyncpg executemany
        records = []
        for i in range(len(texts)):
            r_id = record_ids[i] if record_ids and record_ids[i] else str(uuid.uuid4())
            meta = metadata[i] if metadata else {}
            # pgvector accepts vector as a string representation or a list
            # asyncpg pgvector integration needs string if we don't register the type explicitly,
            # but we can just cast from text: $2::vector
            vec_str = "[" + ",".join(str(v) for v in vectors[i]) + "]"
            records.append((r_id, vec_str, texts[i], json.dumps(meta)))
            
        try:
            async with self.pool.acquire() as conn:
                # Use executemany for batch insertion
                # ON CONFLICT update to allow idempotency
                await conn.executemany(f"""
                    INSERT INTO {table} (id, embedding, text, metadata)
                    VALUES ($1, $2::vector, $3, $4::jsonb)
                    ON CONFLICT (id) DO UPDATE 
                    SET embedding = EXCLUDED.embedding, 
                        text = EXCLUDED.text, 
                        metadata = EXCLUDED.metadata
                """, records)
            return True
        except Exception as exc:
            raise DbError(f"Failed to insert into {collection_name}: {exc}") from exc

    async def delete_by_metadata(self, collection_name: str, key: str, value: str) -> int:
        table = self._table_name(collection_name)
        if not await self.collection_exists(collection_name):
            return 0
            
        try:
            async with self.pool.acquire() as conn:
                # ->> operator extracts jsonb field as text
                result = await conn.execute(f"""
                    DELETE FROM {table} 
                    WHERE metadata->>$1 = $2
                """, key, value)
                
                # format is "DELETE N"
                if result.startswith("DELETE "):
                    return int(result.split(" ")[1])
                return 0
        except Exception as exc:
            raise DbError(f"Failed to delete by metadata in {collection_name}: {exc}") from exc

    async def search_by_vector(
        self,
        collection_name: str,
        vector: list[float],
        limit: int = 5,
        asset_ids: list[str] | None = None,
    ) -> list[dict]:
        table = self._table_name(collection_name)
        if not await self.collection_exists(collection_name):
            return []
            
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        
        # operator based on distance
        operator = "<=>" # cosine
        if self.distance_method == DistanceMethod.DOT:
            operator = "<#>" # pgvector inner product is negative, but we'll just use it for sorting
        elif self.distance_method == DistanceMethod.EUCLID:
            operator = "<->"
            
        query = f"""
            SELECT id, text, metadata, (embedding {operator} $1::vector) as distance
            FROM {table}
        """
        
        args = [vec_str]
        
        if asset_ids:
            # Add filtering by metadata
            # We can check if metadata->>'asset_id' is in the array
            query += " WHERE metadata->>'asset_id' = ANY($2)"
            args.append(asset_ids)
            
        query += f" ORDER BY embedding {operator} $1::vector LIMIT ${len(args) + 1}"
        args.append(limit)
        
        try:
            async with self.pool.acquire() as conn:
                records = await conn.fetch(query, *args)
                
                hits = []
                for r in records:
                    hits.append({
                        "id": r["id"],
                        "score": float(r["distance"]), # note: pgvector returns distance, not score. 
                        "text": r["text"],
                        "metadata": json.loads(r["metadata"]) if r["metadata"] else {}
                    })
                return hits
        except Exception as exc:
            raise DbError(f"Failed to search in {collection_name}: {exc}") from exc
