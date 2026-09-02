"""每日推送内容构建。

与其它指令不同，这里没有消息事件可用（由定时任务触发），因此只继承
Composer，产出一串图片 URL 交给 main.py 分发。

作品与角色两块内容各自独立 try：其中一块检索失败时另一块照常推送，
不会像上游那样一个异常吞掉整次推送。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from astrbot.api import logger

from ..types import Base64, CacheGroup, CommandType, Layout
from ..utils import (
    brief,
    character_lines,
    paragraphs,
    parse_touchgal_details_async,
    vn_lines,
)
from .base import Composer, feature

_LOG = "[视觉小说工房]"

_NO_INTRO = "TouchGal暂无该作品简介。"


class DailyDigest(Composer):
    """构建当天的推送图片。"""

    async def build(self) -> list[str]:
        """返回图片 URL 列表；全部失败时返回空列表。"""
        now = datetime.now()
        date = [str(now.year), f"{now.month:02d}", f"{now.day:02d}"]
        stamp = f"{now.year}年{now.month}月{now.day}日"

        urls: list[str] = []
        for name, builder in (
            ("纪念日作品", self._build_event_vn),
            ("生日角色", self._build_event_cha),
        ):
            try:
                url = await builder(date, stamp)
            except Exception as error:
                logger.warning("%s 构建每日推送（%s）失败：%s", _LOG, name, brief(error))
                continue
            if url:
                urls.append(url)
        return urls

    # -- 简介 -------------------------------------------------------------- #

    async def _intro(self, vn_id: str) -> tuple[list[str], list[Base64]]:
        """去 TouchGal 找中文简介与预览图，失败时给一句占位说明。"""
        try:
            results, _ = await self.runtime.touchgal.request_vn_by_search(
                CommandType.EVENT_TIMED, vn_id
            )
            html = await self.runtime.touchgal.request_html(results[0].uniqueId)
            details = await parse_touchgal_details_async(html)
        except Exception as error:
            logger.debug("%s 获取 %s 的简介失败：%s", _LOG, vn_id, brief(error))
            return [_NO_INTRO], []

        desc = paragraphs(details.description) or [_NO_INTRO]
        previews = await self.covers(details.previews, CacheGroup.TOUCHGAL)
        return desc, previews

    # -- 两种推送形态 ------------------------------------------------------- #

    async def _build_event_vn(self, date: Sequence[str], stamp: str) -> str:
        vn, characters = await self.runtime.vndb.request_by_event_vn(date)
        desc, previews = await self._intro(vn.id)
        cover = (await self.vndb_covers([vn]))[0]
        data = feature(
            main_image=cover,
            subtitle=vn.display_title,
            info=vn_lines(vn),
            title=f"{stamp} · 今日发售纪念",
            desc=desc,
            previews=previews,
            cards_title="登场角色",
            cards=await self.character_cards(characters, ignore_vns=True),
        )
        return await self.render(Layout.FEATURE, data)

    async def _build_event_cha(self, date: Sequence[str], stamp: str) -> str:
        character, vns = await self.runtime.vndb.request_by_event_cha(date)
        cover = (await self.vndb_covers([character]))[0]
        data = feature(
            main_image=cover,
            subtitle=character.display_name,
            info=character_lines(
                character, options=self.settings.character.options, ignore_vns=True
            ),
            title=f"{stamp} · 今日生日",
            cards_title="登场作品",
            cards=await self.vn_cards(vns),
        )
        return await self.render(Layout.FEATURE, data)
