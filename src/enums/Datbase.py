from enum import Enum


class DatabaseCollection(str, Enum):
    PROJECTS = "projects"
    DATA_CHUNKS = "data_chunks"
    ASSETS = "assets"

