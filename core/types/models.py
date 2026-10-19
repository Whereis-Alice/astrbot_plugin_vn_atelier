"""外部 API 响应模型与内部数据结构。

所有对外响应都过一遍 pydantic，好处是字段缺失能第一时间暴露；
坏处是上游一旦返回 null 就会抛 ValidationError，因此凡是官方文档
标注「可能为空」的字段都必须显式写成 X | None = None。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# VNDB
# --------------------------------------------------------------------------- #


class Image(BaseModel):
    """VNDB 图片对象。"""

    url: str
    sexual: float | None = None


class Developer(BaseModel):
    """作品的制作方。"""

    id: str
    original: str | None = None
    name: str


class Title(BaseModel):
    """作品的多语言标题。"""

    lang: str
    title: str
    official: bool


class Vn(BaseModel):
    """作品的精简形态，出现在角色的出场作品列表里。"""

    id: str
    alttitle: str | None = None
    title: str | None = None
    image: Image | None = None
    rating: float | None = None
    released: str | None = None


class VndbVnResponse(BaseModel):
    """VNDB /vn 响应条目。"""

    id: str
    title: str
    alttitle: str | None = None
    # VNDB 对无封面作品返回 null，声明为必填会直接炸掉整条指令。
    image: Image | None = None
    rating: float | None = None
    average: float | None = None
    votecount: int | None = None
    extlinks: list[dict[str, str]] = Field(default_factory=list)
    released: str | None = None
    length_minutes: int | None = None
    platforms: list[str] | None = None
    aliases: list[str] | None = None
    developers: list[Developer] | None = None
    titles: list[Title] | None = None
    # VNDB 官方简介，带自家的标记语法，展示前需过一遍 utils.plain_markup。
    description: str | None = None

    @field_validator("extlinks", mode="before")
    @classmethod
    def clean_extlinks(cls, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        return [
            {"url": item["url"]}
            for item in value
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]

    @property
    def display_title(self) -> str:
        """优先使用罗马音/原文标题，回退到主标题。"""
        return self.alttitle or self.title


class VndbCharacterResponse(BaseModel):
    """VNDB /character 响应条目。"""

    id: str
    name: str
    original: str | None = None
    image: Image | None = None
    birthday: list[int] | None = None
    vns: list[Vn] | None = None
    aliases: list[str] | None = None
    sex: list[str | None] | None = None
    waist: int | None = None
    hips: int | None = None
    bust: int | None = None
    blood_type: str | None = None
    weight: int | None = None
    height: int | None = None
    cup: str | None = None

    @property
    def display_name(self) -> str:
        return self.original or self.name


class VndbProducerResponse(BaseModel):
    """VNDB /producer 响应条目。"""

    id: str
    name: str
    original: str | None = None
    aliases: list[str] | None = None
    lang: str | None = None
    type: str | None = None

    @property
    def display_name(self) -> str:
        return self.original or self.name


# --------------------------------------------------------------------------- #
# TouchGal
# --------------------------------------------------------------------------- #


class TouchGalResponse(BaseModel):
    """TouchGal 搜索结果条目。"""

    id: int
    uniqueId: str
    # 部分条目没有 banner，站点会返回 null。
    banner: str | None = None
    name: str
    type: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)
    platform: list[str] = Field(default_factory=list)
    averageRating: float | None = None


class Link(BaseModel):
    """单条下载链接。"""

    storage: str = ""
    size: str = ""
    content: str = ""
    code: str = ""
    password: str = ""


class ResourceResponse(BaseModel):
    """TouchGal 资源条目。"""

    id: int
    name: str = ""
    section: str = ""
    type: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)
    note: str = ""
    platform: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


class TouchGalDetails(BaseModel):
    """从 TouchGal 详情页 HTML 解析出的补充信息。"""

    third_info: list[str] = Field(default_factory=list)
    """外链信息，形如 ["VNDB ID", "v17"]，没有外链时为空。"""

    previews: list[str] = Field(default_factory=list)
    description: str = ""
    title: str = ""


# --------------------------------------------------------------------------- #
# AnimeTrace
# --------------------------------------------------------------------------- #


class DetectedInfo(BaseModel):
    """一个候选角色。"""

    work: str
    character: str


class AnimeTraceData(BaseModel):
    """一个检测框及其候选角色。"""

    box: tuple[float, float, float, float]
    not_confident: bool = False
    character: list[DetectedInfo] = Field(default_factory=list)


class AnimeTraceResponse(BaseModel):
    """AnimeTrace 识别响应。"""

    code: int
    data: list[AnimeTraceData] = Field(default_factory=list)
    ai: bool = False
    zh_message: str | None = None


# --------------------------------------------------------------------------- #
# 内部状态
# --------------------------------------------------------------------------- #


class RecommendSession(BaseModel):
    """推荐指令的生产者/消费者会话状态。

    生产协程按需翻页拉取候选并提前渲染，消费侧（会话等待器）每次
    取一张已渲染好的图片，从而把用户等待时间压到最低。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    pending: list[TouchGalResponse] = Field(default_factory=list)
    """已拉取但尚未渲染的候选。"""

    ready: list[str] = Field(default_factory=list)
    """已渲染好的图片 URL 队列。"""

    total: int = 0
    """搜索命中的候选总数。"""

    handled: int = 0
    """已消费（渲染或跳过）的候选数。"""

    keyword: str = ""

    ready_signal: asyncio.Event = Field(default_factory=asyncio.Event)
    use_signal: asyncio.Event = Field(default_factory=asyncio.Event)
    stop_signal: asyncio.Event = Field(default_factory=asyncio.Event)
    stop_info: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """便于日志输出的轻量快照。"""
        return {
            "keyword": self.keyword,
            "total": self.total,
            "handled": self.handled,
            "pending": len(self.pending),
            "ready": len(self.ready),
        }
