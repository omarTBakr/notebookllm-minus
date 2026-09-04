from .db_schema import (
    Asset,
    Chat,
    DataChunk,
    Message,
    Project,
    Session,
    TaskExecution,
    User,
    summarize_result,
)


def AssetModel(db):
    return db.assets()


def ChunkModel(db):
    return db.chunks()


def ChatModel(db):
    return db.chats()


def MessageModel(db):
    return db.messages()


def SessionModel(db):
    return db.sessions()


def UserModel(db):
    return db.users()


def ProjectModel(db):
    return db.projects()


def TaskModel(db):
    return db.tasks()


__all__ = [
    "AssetModel",
    "ChunkModel",
    "ChatModel",
    "MessageModel",
    "SessionModel",
    "UserModel",
    "ProjectModel",
    "TaskModel",
    "Asset",
    "Chat",
    "DataChunk",
    "Message",
    "Project",
    "Session",
    "TaskExecution",
    "User",
    "summarize_result",
]
