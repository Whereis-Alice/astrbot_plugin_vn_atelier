"""Bangumi 优先的游戏查询、详情和评分榜。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..service.bangumi import subject_id
from ..types import CacheGroup, CommandType, Layout, Tips
from ..types.bangumi import BangumiSubject
from ..utils import paragraphs, plain_markup
from ..utils.bangumi import bangumi_lines, distribution_data, rating_data
from .base import BaseCommand, feature, gallery
from .ranking import RankingCommand


class BangumiCommand(BaseCommand):
    command = CommandType.BANGUMI

    async def detail_data(self, item: BangumiSubject) -> dict:
        return feature(
            main_image=await self.cover(item.cover, CacheGroup.BANGUMI),
            subtitle=item.display_title,
            title="Bangumi 游戏档案",
            info=bangumi_lines(item),
            desc=paragraphs(plain_markup(item.summary))[:12],
            ratings=rating_data(item),
            distribution=distribution_data(item) if item.rating.value is not None else [],
        )

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        keyword = self.require(value)
        identifier = subject_id(keyword)
        if keyword.lower().startswith(("http://", "https://")) and identifier is None:
            raise Tips("请使用 Bangumi 游戏条目链接，例如 https://bgm.tv/subject/13。")
        if identifier is not None:
            item = await self.runtime.bangumi.subject(identifier)
            if item is None:
                raise Tips("没有可显示的 Bangumi 游戏条目；请检查 ID、条目类型和 NSFW 设置。")
            yield await self.picture(await self.detail_data(item), layout=Layout.FEATURE)
            return
        items = await self.runtime.bangumi.search(keyword)
        if not items:
            raise Tips("Bangumi 没有找到匹配的游戏条目。")
        if len(items) == 1:
            detail = await self.runtime.bangumi.subject(items[0].id)
            if detail is None:
                raise Tips("该 Bangumi 条目目前不可显示。")
            yield await self.picture(await self.detail_data(detail), layout=Layout.FEATURE)
        else:
            yield await self.picture(
                gallery(
                    cards=await self.bangumi_cards(items),
                    title="Bangumi 游戏检索",
                    subtitle=keyword,
                ),
                layout=Layout.GALLERY,
            )


class BangumiRankingCommand(RankingCommand):
    command = CommandType.BANGUMI_RANKING

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        year, tag = self._split(value)
        items = await self.runtime.bangumi.ranking(year, tag)
        if not items:
            raise Tips("没有满足当前年份、标签及评价人数门槛的 Bangumi 游戏。")
        data = gallery(
            cards=await self.bangumi_cards(
                items, badges=[f"NO.{i}" for i in range(1, len(items) + 1)]
            ),
            title="Bangumi 游戏评分榜",
            subtitle=f"{year or '历年'} · {tag or '全部游戏'} · 至少 {self.settings.bangumi.min_votes} 人评价 · 按评分排序",
        )
        yield await self.picture(data, layout=Layout.GALLERY)
