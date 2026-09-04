from enum import Enum


class DatabaseCollection(str, Enum):
    PROJECTS = "projects"
    DATA_CHUNKS = "data_chunks"
    ASSETS = "assets"
    TASK_EXECUTIONS = "task_executions"

    # --- conversations ---
    USERS = "users"
    SESSIONS = "sessions"
    CHATS = "chats"
    MESSAGES = "messages"
