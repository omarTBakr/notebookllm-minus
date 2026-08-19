from motor.motor_asyncio import AsyncIOMotorClient

from enums import DatabaseCollection
from utils import get_logger


class BaseModel:

    def __init__(self, db: AsyncIOMotorClient, collection_name: DatabaseCollection):
        self.db = db
        self.collection = db[collection_name.value]
        # e.g. "models.ProjectModel" — every model inherits a logger named
        # after its own module, matching the controllers' convention.
        self.logger = get_logger(type(self).__module__)
        self.logger.debug(
            "%s bound to collection %r", type(self).__name__, collection_name.value
        )

    # --- index helpers --------------------------------------------------------
    # Hoisted here from ProjectModel/AssetModel/ChunkModel, which each carried a
    # verbatim copy. Every model needs them, none of them needs a different
    # version, and the conversation models would have made it seven copies.

    def get_index(
        self,
        keys: list[tuple[str, int]],  # e.g. [("project_id", 1), ("created_at", -1)]
        unique: bool = False,
        name: str | None = None,
    ) -> dict:
        """Build an index spec dict for create_index.

        keys: list of (field, direction) tuples — 1=ASC, -1=DESC
        """
        auto_name = "_".join(
            f"{field}_{'asc' if direction == 1 else 'desc'}" for field, direction in keys
        ) + "_idx"

        return {
            "key": keys,
            "name": name or auto_name,
            "unique": unique,
        }

    async def create_index(
        self,
        keys: list[tuple[str, int]],
        unique: bool = False,
        name: str | None = None,
    ) -> str:
        index = self.get_index(keys, unique, name)
        return await self.collection.create_index(
            index["key"], name=index["name"], unique=index["unique"]
        )
