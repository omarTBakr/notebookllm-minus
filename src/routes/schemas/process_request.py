from pydantic import BaseModel, Field, model_validator


class ProcessRequest(BaseModel):
    asset_id: str | None = Field(None, description="UUID of the asset to process")
    chunk_size: int = Field(100, gt=0)
    overlap_size: int = Field(20, ge=0)
    reset: bool = False

    @model_validator(mode="after")
    def check_overlap_fits_chunk(self):
        # The splitter rejects this combination anyway; catching it here turns a
        # server error into a clear 422 naming the offending fields.
        if self.overlap_size >= self.chunk_size:
            raise ValueError(
                f"overlap_size ({self.overlap_size}) must be smaller than "
                f"chunk_size ({self.chunk_size})"
            )
        return self
