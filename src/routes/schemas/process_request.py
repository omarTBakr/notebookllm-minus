from pydantic import BaseModel, Field, model_validator


class ProcessRequest(BaseModel):
    asset_id: str | None = Field(None, description="UUID of the asset to process")
    # Matches ProcessController's own default and the UI's CHAT_CHUNK_SIZE. The
    # old 100 produced roughly ten rows per page of a PDF, which is the number
    # that makes the ingest INSERT and the embedding pass expensive.
    chunk_size: int = Field(1000, gt=0)
    overlap_size: int = Field(200, ge=0)
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
