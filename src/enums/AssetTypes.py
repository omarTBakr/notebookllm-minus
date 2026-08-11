from enum import Enum


class AssetType(str, Enum):
    TEXT  = "text"
    AUDIO = "audio"
    IMAGE = "image"
    PDF   = "pdf"
    DOCX  = "docx"
    XLSX  = "xlsx"
    CSV   = "csv"
    HTML  = "html"
    JSON  = "json"
    XML   = "xml"
    OTHER = "other"

    # Maps every known MIME type to the matching enum member.
    # Defined at class level so it is built once and shared.
    _mime_map_: dict[str, "AssetType"]  # populated after class body

    @classmethod
    def from_content_type(cls, content_type: str | None) -> "AssetType":
        """Return the AssetType that matches *content_type*, or OTHER."""
        return cls._mime_map_.get(content_type or "", cls.OTHER)


# Build the reverse-lookup table after the class exists so we can reference
# enum members by name without forward-declaration gymnastics.
AssetType._mime_map_ = {
    "application/pdf":          AssetType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": AssetType.DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       AssetType.XLSX,
    "text/csv":                 AssetType.CSV,
    "text/plain":               AssetType.TEXT,
    "text/html":                AssetType.HTML,
    "application/json":         AssetType.JSON,
    "application/xml":          AssetType.XML,
    "text/xml":                 AssetType.XML,
    "image/jpeg":               AssetType.IMAGE,
    "image/png":                AssetType.IMAGE,
    "image/gif":                AssetType.IMAGE,
    "image/webp":               AssetType.IMAGE,
    "audio/mpeg":               AssetType.AUDIO,
    "audio/wav":                AssetType.AUDIO,
}