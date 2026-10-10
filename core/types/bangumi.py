"""Bangumi 游戏条目及评分，保持原站的 10 分制。"""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class BangumiRating(BaseModel):
    score: float = 0
    total: int = 0
    rank: int = 0
    count: dict[str, int] = Field(default_factory=dict)

    @field_validator("score", "total", "rank", mode="before")
    @classmethod
    def null_number(cls, value: Any) -> Any:
        return 0 if value is None else value

    @field_validator("count", mode="before")
    @classmethod
    def clean_count(cls, value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): max(0, int(amount))
            for key, amount in value.items()
            if str(key) in {str(i) for i in range(1, 11)} and str(amount).isdigit()
        }

    @property
    def value(self) -> float | None:
        if self.total <= 0 or not math.isfinite(self.score) or not 0 < self.score <= 10:
            return None
        return self.score


def infobox_values(value: Any) -> list[str]:
    """Infobox 的值可能是文本，也可能是包含 k/v 的数组。"""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in infobox_values(item)]
    if isinstance(value, dict):
        return infobox_values(value.get("v"))
    return []


class BangumiSubject(BaseModel):
    id: int = Field(gt=0)
    type: int
    name: str = ""
    name_cn: str = ""
    summary: str = ""
    date: str | None = None
    platform: str = ""
    nsfw: bool = False
    images: dict[str, str] = Field(default_factory=dict)
    rating: BangumiRating = Field(default_factory=BangumiRating)
    collection: dict[str, int] = Field(default_factory=dict)
    tags: list[dict[str, Any]] = Field(default_factory=list)
    infobox: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("name", "name_cn", "summary", "platform", mode="before")
    @classmethod
    def null_text(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("images", "collection", mode="before")
    @classmethod
    def null_mapping(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @field_validator("tags", "infobox", mode="before")
    @classmethod
    def null_list(cls, value: Any) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @field_validator("rating", mode="before")
    @classmethod
    def null_rating(cls, value: Any) -> Any:
        return value if isinstance(value, (dict, BangumiRating)) else {}

    @property
    def display_title(self) -> str:
        return self.name_cn or self.name or f"Bangumi #{self.id}"

    @property
    def url(self) -> str:
        return f"https://bgm.tv/subject/{self.id}"

    @property
    def cover(self) -> str:
        return self.images.get("large") or self.images.get("common") or ""

    def info(self, *keys: str) -> list[str]:
        return [
            text
            for item in self.infobox
            if item.get("key") in keys
            for text in infobox_values(item.get("value"))
        ]

    @property
    def names(self) -> list[str]:
        return [name for name in [self.name, self.name_cn, *self.info("别名")] if name]

    @property
    def vndb_id(self) -> str:
        for item in self.infobox:
            for text in infobox_values(item.get("value")):
                try:
                    url = urlsplit(text)
                except ValueError:
                    continue
                if (
                    url.scheme in {"http", "https"}
                    and url.hostname in {"vndb.org", "www.vndb.org"}
                    and re.fullmatch(r"/v[1-9]\d*/?", url.path)
                ):
                    return url.path.strip("/")
        return ""
