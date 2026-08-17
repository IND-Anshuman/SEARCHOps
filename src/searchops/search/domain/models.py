from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class SearchCapability(str, Enum):
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    NEWS = "news"
    IMAGES = "images"
    VIDEOS = "videos"
    FRESHNESS = "freshness"
    LOCALIZATION = "localization"
    JAVASCRIPT = "javascript"
    MARKDOWN = "markdown"
    METADATA = "metadata"

class SearchProfile(str, Enum):
    FAST = "fast"
    DEEP = "deep"
    ACADEMIC = "academic"
    NEWS = "news"

class NormalizedSearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    provider: str
    score: float = 1.0
    rank: int = 1
    published_at: str | None = None
    favicon: str | None = None
    language: str | None = None
    source_type: str = "text"
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
