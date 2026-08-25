from datetime import datetime, timezone
from typing import Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator
from enums import AssetType
from utils import get_settings

# Schema-level ceiling on a single asset's bytes. Sourced from .env's
# MAX_FILE_SIZE so the upload check (DataController) and this model can never
# disagree about the limit.
_MAX_ASSET_BYTES = get_settings().MAX_FILE_SIZE

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

    # sha256 of file_bytes, hex. What makes "the same document" a question the
    # database can answer: asset_id is a fresh uuid on every upload, and the
    # filename is neither stable (rename) nor meaningful (two files can share
    # one). Unique per project, so the same file may live in several notebooks.
    content_hash: str = Field("", max_length=64)

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
