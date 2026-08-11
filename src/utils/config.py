from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SRC_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    APPLICATION_NAME: str
    APP_VERSION: str

    ALLOWED_TYPES: list
    MAX_FILE_CHUNK_SIZE: int
    MAX_ASSET_SIZE_BYTES: int    # hard ceiling on stored binary (bytes)
 
    MAX_FILE_SIZE: int = 10485760  # resolved in model_post_init below

    # mongo db configurations
    MONGO_URI: str
    MONGO_DB_NAME: str

    # logging configurations
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # "text" for humans, "json" for log aggregators
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True
    LOG_DIR: str = "logs"  # relative paths resolve against src/
    LOG_FILE_NAME: str = "notebookllm-minus.log"
    LOG_MAX_BYTES: int = 10485760  # 10 MB per file before rotating
    LOG_BACKUP_COUNT: int = 5

    @field_validator("LOG_LEVEL")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}")
        return level

    @field_validator("LOG_FORMAT")
    @classmethod
    def _normalize_log_format(cls, value: str) -> str:
        fmt = value.strip().lower()
        if fmt not in {"text", "json"}:
            raise ValueError(f"LOG_FORMAT must be 'text' or 'json', got {value!r}")
        return fmt

    @property
    def log_file_path(self) -> Path:
        """Absolute path of the log file, anchoring a relative LOG_DIR to src/."""
        log_dir = Path(self.LOG_DIR)
        if not log_dir.is_absolute():
            log_dir = SRC_DIR / log_dir
        return log_dir / self.LOG_FILE_NAME

    model_config = SettingsConfigDict(
        env_file=SRC_DIR / ".env",  # resolves to src/.env regardless of CWD
        case_sensitive=False,
        extra="ignore"
    )

def get_settings() -> Settings:
    return Settings()
