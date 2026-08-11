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
