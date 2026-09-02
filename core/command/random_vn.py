"""随机作品：工房 随机。

流程是「TouchGal 随机取一个作品 → 解析详情页 → 回查站内搜索补齐结构化
信息」。最后一步需要一个关键词，这里按「页面标题 → 外链值 → uniqueId」
依次尝试，任一命中即止；上游只用外链值一条路，遇到没有外链的作品就直接
报错。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..types import CacheGroup, CommandType, NoResultException, TouchGalResponse
from ..utils import paragraphs, parse_touchgal_details_async, search_lines
from .base import BaseCommand, feature


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
        raise NoResultException(self.command.value, keywords[-1] if keywords else "")

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
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

        data = feature(
            main_image=await self.cover(game.banner, CacheGroup.TOUCHGAL),
            subtitle=game.name or details.title,
            info=info,
            title="随机抽取",
            desc=paragraphs(details.description),
            previews=await self.covers(details.previews, CacheGroup.TOUCHGAL),
        )
        yield await self.picture(data)
