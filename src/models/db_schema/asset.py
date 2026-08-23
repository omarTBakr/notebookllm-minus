from datetime import datetime, timezone
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator
from enums import AssetType

# These match the defaults in .env — they are schema-level ceilings, not
# runtime limits. Update here if you change the .env values significantly.
_MAX_ASSET_BYTES    = 10_485_760     # 10 MB            (MAX_ASSET_SIZE_BYTES)

def utcnow() -> datetime:
    """Timezone-aware UTC. Naive local times sort wrongly across DST."""
    return datetime.now(timezone.utc)


class Asset(BaseModel):
    # populate_by_name lets the model be built either from Mongo documents
    # (`_id`) or from keyword arguments (`id=`).
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    id: Optional[ObjectId] = Field(default_factory=ObjectId, alias="_id")
    asset_id: str = Field(..., min_length=1, max_length=200)
    asset_type: AssetType = Field(...)
    
    project_id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=1000)
    
    file_bytes: bytes = Field(default=b"", max_length=_MAX_ASSET_BYTES)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    
    @field_validator("name")
    @classmethod
    def name_has_visible_text(cls, value: str) -> str:
        # A field validator runs *after* type coercion, so a non-string `name`
        # reports as a normal validation error instead of raising AttributeError.
        # Deliberately permissive about punctuation: `name` holds human text
        # (often a filename) and never reaches the filesystem.
        if not value.strip():
            raise ValueError("name must contain visible characters")
        return value.strip()
