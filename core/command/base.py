"""指令基类与布局数据构造器。

三层职责划分：

* 布局构造函数（card / gallery / dossier / feature / panel）
  —— 只负责把纯文本拼成模板认得的字典结构，不碰网络也不碰事件。
* Composer
  —— 负责「取图 + 组卡片 + 渲染」，与消息事件无关，因此定时推送
     （core.command.daily）也能复用同一套外观。
* BaseCommand
  —— 在 Composer 之上加入事件相关能力：参数校验、图片/文本结果、
     转发支持判断。

上游把这些逻辑混在一个巨型 Command 类里，且渲染失败就直接抛异常；
这里统一提供纯文本兜底，渲染服务不可用时指令依然能给出结果。
"""

from __future__ import annotations

import calendar
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from typing import Any, ClassVar

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..runtime import AtelierRuntime
from ..settings import Settings
from ..types import (
    LAYOUT_OF_COMMAND,
    ArgsOrNullException,
    Base64,
    CacheGroup,
    CommandType,
    InvalidArgsException,
    Layout,
    RenderException,
    VndbCharacterResponse,
    VndbProducerResponse,
    VndbVnResponse,
)
from ..utils import character_lines, producer_lines, vn_lines

_LOG = "[视觉小说工房]"

# 支持合并转发的平台。上游把 qq_official 也算进去，但官方机器人接口
# 并不支持合并转发节点，实际会静默丢消息。
FORWARD_PLATFORMS = frozenset({"aiocqhttp", "onebot"})

# 日期参数允许的分隔符。
_DATE_SEPARATORS = ("-", "/", ".")


# --------------------------------------------------------------------------- #
# 布局数据构造
# --------------------------------------------------------------------------- #


def _clean(items: Iterable[Any]) -> list[str]:
    """过滤空行并统一成字符串。"""
    return [str(item) for item in items if str(item).strip()]


def card(
    *,
    image: Base64,
    subtitle: str = "",
    desc: Sequence[str] = (),
    badge: str = "",
) -> dict[str, Any]:
    """一张封面卡：左图右文。"""
    result: dict[str, Any] = {"image": image}
    if subtitle:
        result["subtitle"] = subtitle
    lines = _clean(desc)
    if lines:
        result["desc"] = lines
    if badge:
        result["badge"] = badge
    return result


def section(
    *,
    text: Sequence[str],
    image: Base64 = "",
    cards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """档案布局里的一个小节：一段抬头 + 若干卡片。"""
    header: dict[str, Any] = {"text": _clean(text)}
    if image:
        header["image"] = image
    result: dict[str, Any] = {"header": header}
    if cards:
        result["cards"] = list(cards)
    return result


def gallery(
    *,
    cards: Sequence[Mapping[str, Any]],
    title: str = "",
    subtitle: str = "",
) -> dict[str, Any]:
    """画廊：等宽卡片纵向排列，用于搜索结果。"""
    result: dict[str, Any] = {"cards": list(cards)}
    if title:
        result["title"] = title
    if subtitle:
        result["subtitle"] = subtitle
    return result


def dossier(
    *,
    sections: Sequence[Mapping[str, Any]],
    title: str = "",
    subtitle: str = "",
    hero: Mapping[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """档案：分组展示，用于厂商 / 简讯 / 出处识别。"""
    result: dict[str, Any] = {"sections": list(sections)}
    if title:
        result["title"] = title
    if subtitle:
        result["subtitle"] = subtitle
    if hero:
        result["hero"] = dict(hero)
    note_lines = _clean(notes)
    if note_lines:
        result["notes"] = note_lines
    return result


def feature(
    *,
    main_image: Base64,
    subtitle: str,
    info: Sequence[str],
    title: str = "",
    desc: Sequence[str] = (),
    previews: Sequence[Base64] = (),
    cards_title: str = "",
    cards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """特写：大封面 + 简介 + 预览图，用于单个作品的详情。"""
    result: dict[str, Any] = {
        "main_image": main_image,
        "subtitle": subtitle,
        "info": _clean(info),
    }
    if title:
        result["title"] = title
    desc_lines = _clean(desc)
    if desc_lines:
        result["desc"] = desc_lines
    if previews:
        result["previews"] = list(previews)
    if cards:
        result["cards"] = list(cards)
        if cards_title:
            result["cards_title"] = cards_title
    return result


def panel(
    *,
    groups: Sequence[Mapping[str, Any]],
    title: str = "",
    subtitle: str = "",
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """面板：纯文字表格，用于帮助 / 收藏夹 / 缓存统计。"""
    result: dict[str, Any] = {"groups": list(groups)}
    if title:
        result["title"] = title
    if subtitle:
        result["subtitle"] = subtitle
    note_lines = _clean(notes)
    if note_lines:
        result["notes"] = note_lines
    return result


def row(label: str, value: str) -> dict[str, str]:
    """面板里的一行。"""
    return {"label": label, "value": value}


def group(name: str, rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """面板里的一组。"""
    return {"name": name, "rows": list(rows)}


# --------------------------------------------------------------------------- #
# Composer
# --------------------------------------------------------------------------- #


class Composer:
    """把 API 响应组装成布局数据并渲染。不依赖消息事件。"""

    def __init__(self, runtime: AtelierRuntime) -> None:
        self.runtime = runtime

    @property
    def settings(self) -> Settings:
        return self.runtime.settings

    # -- 取图 -------------------------------------------------------------- #

    async def error_image(self, *, prefix: bool = True) -> Base64:
        return await self.runtime.assets.error_image(prefix=prefix)

    async def cover(
        self,
        url: str | None,
        group: CacheGroup = CacheGroup.VNDB,
        *,
        prefix: bool = True,
    ) -> Base64:
        return await self.runtime.assets.fetch(group, url or "", prefix=prefix)

    async def covers(
        self,
        urls: Iterable[str],
        group: CacheGroup = CacheGroup.VNDB,
        *,
        prefix: bool = True,
    ) -> list[Base64]:
        return await self.runtime.assets.batch(urls, group, prefix=prefix)

    async def vndb_covers(self, responses: Sequence[Any]) -> list[Base64]:
        return await self.runtime.assets.vndb_covers(responses)

    # -- 组卡片 ------------------------------------------------------------ #

    async def vn_cards(
        self,
        results: Sequence[VndbVnResponse],
        *,
        badges: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        images = await self.vndb_covers(results)
        return [
            card(
                image=image,
                subtitle=vn.display_title,
                desc=vn_lines(vn),
                badge=badges[index] if index < len(badges) else "",
            )
            for index, (vn, image) in enumerate(zip(results, images, strict=True))
        ]

    async def character_cards(
        self,
        results: Sequence[VndbCharacterResponse],
        *,
        ignore_vns: bool = False,
    ) -> list[dict[str, Any]]:
        images = await self.vndb_covers(results)
        options = self.settings.character.options
        return [
            card(
                image=image,
                subtitle=character.display_name,
                desc=character_lines(character, options=options, ignore_vns=ignore_vns),
            )
            for character, image in zip(results, images, strict=True)
        ]

    # -- 组布局 ------------------------------------------------------------ #

    async def _view(
        self,
        cards: Sequence[Mapping[str, Any]],
        *,
        desc: Sequence[str],
        previews: Sequence[Base64],
        title: str,
        subtitle: str,
    ) -> tuple[Layout, dict[str, Any]]:
        """有简介就用特写布局，否则用画廊。"""
        if not desc or not cards:
            return Layout.GALLERY, gallery(cards=cards, title=title, subtitle=subtitle)
        head = cards[0]
        return Layout.FEATURE, feature(
            main_image=str(head.get("image", "")),
            subtitle=str(head.get("subtitle", "")) or subtitle,
            info=head.get("desc") or [],
            title=title,
            desc=desc,
            previews=previews,
        )

    async def vn_view(
        self,
        results: Sequence[VndbVnResponse],
        *,
        desc: Sequence[str] = (),
        previews: Sequence[Base64] = (),
        title: str = "",
        subtitle: str = "",
        badges: Sequence[str] = (),
    ) -> tuple[Layout, dict[str, Any]]:
        cards = await self.vn_cards(results, badges=badges)
        return await self._view(cards, desc=desc, previews=previews, title=title, subtitle=subtitle)

    async def character_view(
        self,
        results: Sequence[VndbCharacterResponse],
        *,
        desc: Sequence[str] = (),
        previews: Sequence[Base64] = (),
        title: str = "",
        subtitle: str = "",
    ) -> tuple[Layout, dict[str, Any]]:
        cards = await self.character_cards(results)
        return await self._view(cards, desc=desc, previews=previews, title=title, subtitle=subtitle)

    async def producer_view(
        self,
        producers: Sequence[VndbProducerResponse],
        groups: Sequence[Sequence[VndbVnResponse]],
        *,
        title: str = "",
        subtitle: str = "",
        notes: Sequence[str] = (),
    ) -> tuple[Layout, dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for index, producer in enumerate(producers):
            works = list(groups[index]) if index < len(groups) else []
            blocks.append(
                section(
                    text=producer_lines(producer),
                    cards=await self.vn_cards(works),
                )
            )
        return Layout.DOSSIER, dossier(sections=blocks, title=title, subtitle=subtitle, notes=notes)

    # -- 渲染 -------------------------------------------------------------- #

    async def render(
        self,
        layout: Layout,
        data: Mapping[str, Any],
        *,
        options: dict[str, Any] | None = None,
    ) -> str:
        return await self.runtime.renderer.render(layout, data, options=options)


# --------------------------------------------------------------------------- #
# BaseCommand
# --------------------------------------------------------------------------- #


class BaseCommand(Composer, ABC):
    """一条子指令的处理器。"""

    command: ClassVar[CommandType]
    #: 管理类指令不参与冷却，避免运维操作被自己的限流挡住。
    cooldown: ClassVar[bool] = True

    def __init__(self, runtime: AtelierRuntime, event: AstrMessageEvent) -> None:
        super().__init__(runtime)
        self.event = event

    # -- 环境 -------------------------------------------------------------- #

    @property
    def layout(self) -> Layout:
        return LAYOUT_OF_COMMAND.get(self.command, Layout.GALLERY)

    @property
    def supports_forward(self) -> bool:
        return self.event.get_platform_name() in FORWARD_PLATFORMS

    # -- 输出 -------------------------------------------------------------- #

    async def picture(
        self,
        data: Mapping[str, Any],
        *,
        layout: Layout | None = None,
        options: dict[str, Any] | None = None,
    ) -> MessageEventResult:
        """渲染成图片；渲染不可用时按配置降级为纯文本。"""
        try:
            url = await self.render(layout or self.layout, data, options=options)
        except RenderException as error:
            if not self.settings.basic.text_fallback:
                raise
            logger.warning("%s 渲染失败，降级为纯文本：%s", _LOG, error.message)
            return self.event.plain_result(self.runtime.renderer.plain_text(data))
        return self.event.image_result(url)

    def text(self, message: str) -> MessageEventResult:
        """纯文本结果。注意：绝不要直接 yield 字符串，框架不接受。"""
        return self.event.plain_result(message)

    # -- 参数 -------------------------------------------------------------- #

    def require(self, value: str | None) -> str:
        """参数不能为空。"""
        text = (value or "").strip()
        if not text:
            raise ArgsOrNullException(self.command.value, "")
        return text

    def split_date(self, value: str) -> tuple[int, int]:
        """解析「月-日」参数，返回 (月, 日)。

        允许 - / . 三种分隔符；日的上界按闰年计算，因此 2-29 合法。
        """
        raw = value.strip()
        separator = next((s for s in _DATE_SEPARATORS if s in raw), "")
        if not separator:
            raise InvalidArgsException(self.command.value, raw)
        parts = [part.strip() for part in raw.split(separator) if part.strip()]
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise InvalidArgsException(self.command.value, raw)

        month, day = int(parts[0]), int(parts[1])
        if not 1 <= month <= 12:
            raise InvalidArgsException(self.command.value, raw)
        # 2000 是闰年，用它取月份天数即可放行 2-29。
        if not 1 <= day <= calendar.monthrange(2000, month)[1]:
            raise InvalidArgsException(self.command.value, raw)
        return month, day

    # -- 执行 -------------------------------------------------------------- #

    @abstractmethod
    def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        """子类实现为 async def + yield。"""
        raise NotImplementedError
