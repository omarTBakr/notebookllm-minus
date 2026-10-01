from .artifact import (
    Artifact,
    ChunkSummary,
    Flashcard,
    FlashcardSet,
    MindMapBranch,
    MindMapNode,
    MindMapNodeSet,
    MindMapOutline,
    QuizQuestion,
    QuizSet,
    SummarySet,
)
from .asset import Asset
from .conversation import Chat, Message, PersonalUserInfo, Session, User
from .data_chunk import DataChunk
from .ingest import IngestBatch, IngestRun
from .project import Project
from .task_execution import TaskExecution, summarize_result


def ArtifactModel(db):
    """Study material generated from a notebook's documents."""
    return db.artifacts()


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


def PersonalUserInfoModel(db):
    """Durable facts extracted from a user's `/memory` messages."""
    return db.personal_user_info()


def ProjectModel(db):
    return db.projects()


def IngestBatchModel(db):
    """Parsed pages held between the stages of one ingestion."""
    return db.ingest_batches()


def TaskModel(db):
    return db.tasks()


__all__ = [
    "ArtifactModel",
    "AssetModel",
    "ChunkModel",
    "ChatModel",
    "MessageModel",
    "SessionModel",
    "UserModel",
    "PersonalUserInfoModel",
    "ProjectModel",
    "IngestBatchModel",
    "TaskModel",
    "Artifact",
    "Asset",
    "Chat",
    "ChunkSummary",
    "DataChunk",
    "Flashcard",
    "FlashcardSet",
    "MindMapBranch",
    "MindMapNode",
    "MindMapNodeSet",
    "MindMapOutline",
    "Message",
    "PersonalUserInfo",
    "Project",
    "QuizQuestion",
    "QuizSet",
    "Session",
    "SummarySet",
    "TaskExecution",
    "IngestBatch",
    "IngestRun",
    "User",
    "summarize_result",
]
