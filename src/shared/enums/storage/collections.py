from enum import Enum


class DatabaseCollection(str, Enum):
    PROJECTS = "projects"
    DATA_CHUNKS = "data_chunks"
    ASSETS = "assets"
    TASK_EXECUTIONS = "task_executions"
    ARTIFACTS = "artifacts"
    # Scratch, for the length of one ingestion: parsed pages waiting for
    # the rest of their document. Deleted once it is chunked.
    INGEST_BATCHES = "ingest_batches"
    INGEST_RUNS = "ingest_runs"

    # --- conversations ---
    USERS = "users"
    SESSIONS = "sessions"
    CHATS = "chats"
    MESSAGES = "messages"
