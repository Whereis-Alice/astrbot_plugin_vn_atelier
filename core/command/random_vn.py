"""随机作品：工房 随机。

主链路是「TouchGal 随机取一个作品 → 解析详情页 → 回查站内搜索补齐结构化
信息」。最后一步需要一个关键词，这里按「页面标题 → 外链值 → uniqueId」
依次尝试，任一命中即止；上游只用外链值一条路，遇到没有外链的作品就直接
报错。

备选链路走 VNDB。TouchGal 前置了 Cloudflare，一旦站点开启浏览器验证（或
换了域名），整条主链路会全线 403。此时改由 VNDB 抽取，指令仍有输出——比
扔一句「网络错误」有用得多。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageEventResult

from ..types import (
    CacheGroup,
    ChallengeException,
    CommandType,
    InternetException,
    NoResultException,
    TouchGalResponse,
)
from ..utils import (
    brief,
    paragraphs,
    parse_touchgal_details_async,
    plain_markup,
    search_lines,
    vn_lines,
)
from .base import BaseCommand, feature

_LOG = "[Gal工房]"

_VNDB_DESC_LIMIT = 12
"""VNDB 简介最多保留的段落数。个别条目的简介很长，全量渲染会把卡片拉爆。"""


class RandomCommand(BaseCommand):
    command = CommandType.RANDOM

    async def _lookup(self, keywords: list[str]) -> TouchGalResponse:
        """依次用候选关键词回查站内搜索。"""
        for keyword in keywords:
            if not keyword:
                continue
            try:
                results, _ = await self.runtime.touchgal.request_vn_by_search(self.command, keyword)
            except NoResultException:
                continue
            if results:
                return results[0]
        raise NoResultException(self.command, keywords[-1] if keywords else "")

    # -- 主链路：TouchGal -------------------------------------------------- #

    async def _from_touchgal(self) -> dict[str, Any]:
        touchgal = self.runtime.touchgal
        unique_id = await touchgal.request_random()
        details = await parse_touchgal_details_async(await touchgal.request_html(unique_id))

        third = details.third_info
        candidates = [details.title]
        if len(third) >= 2:
            candidates.append(third[1])
        candidates.append(unique_id)
        game = await self._lookup(candidates)

        info = search_lines(game, ignore_name=True)
        if len(third) >= 2:
            info.insert(0, f"{third[0]}：{third[1]}")

        return feature(
            main_image=await self.cover(game.banner, CacheGroup.TOUCHGAL),
            subtitle=game.name or details.title,
            info=info,
            title="随机抽取",
            desc=paragraphs(details.description),
            previews=await self.covers(details.previews, CacheGroup.TOUCHGAL),
        )

    # -- 备选链路：VNDB ---------------------------------------------------- #

    async def _from_vndb(self) -> dict[str, Any]:
        vn = await self.runtime.vndb.request_random_vn()
        info = vn_lines(vn)
        info.append("数据源：VNDB")
        covers = await self.vndb_covers([vn])
        return feature(
            main_image=covers[0] if covers else await self.error_image(),
            subtitle=vn.display_title,
            info=info,
            title="随机抽取",
            desc=paragraphs(plain_markup(vn.description), _VNDB_DESC_LIMIT),
        )

    # -- 执行 -------------------------------------------------------------- #

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        data: dict[str, Any] | None = None

        waiting = self.runtime.touchgal.cooldown
        if waiting:
            # 站点正处于浏览器验证熔断期，这一趟注定 403，直接省掉。
            logger.info("%s TouchGal 验证冷却中（剩余 %ss），本次改用 VNDB 抽取", _LOG, waiting)
        else:
            try:
                data = await self._from_touchgal()
            except (ChallengeException, InternetException) as error:
                logger.warning(
                    "%s TouchGal 取随机作品失败（%s），改用 VNDB 抽取", _LOG, brief(error)
                )

        if data is None:
            data = await self._from_vndb()
        yield await self.picture(data)
