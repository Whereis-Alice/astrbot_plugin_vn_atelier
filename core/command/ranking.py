"""评分排行：工房 排行 [年份] [标签]。

参数里的 4 位数字当年份，其余当标签名。两者都可省略，省略即全站总榜。
上游没有这个功能，这里补上——VNDB 的评分数据本身就支持按标签与年份
过滤，只是需要先把标签名换成标签 ID。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from astrbot.api.event import MessageEventResult

from ..types import CommandType
from .base import BaseCommand

_MIN_YEAR = 1970


class RankingCommand(BaseCommand):
    command = CommandType.RANKING

    def _split(self, value: str) -> tuple[int | None, str]:
        """把参数拆成 (年份, 标签名)。"""
        limit = datetime.now().year + 1
        year: int | None = None
        words: list[str] = []
        for token in value.split():
            if year is None and len(token) == 4 and token.isdigit():
                candidate = int(token)
                if _MIN_YEAR <= candidate <= limit:
                    year = candidate
                    continue
            words.append(token)
        return year, " ".join(words).strip()

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        year, tag_name = self._split(value.strip())

        tag_id = ""
        label = "全站"
        if tag_name:
            tag_id, label = await self.runtime.vndb.resolve_tag(tag_name)

        results = await self.runtime.vndb.request_ranking(
            year=year, tag_id=tag_id or None, label=label
        )
        badges = [f"NO.{index}" for index in range(1, len(results) + 1)]
        scope = f"{year}年" if year else "历年"
        layout, data = await self.vn_view(
            results,
            badges=badges,
            title="VNDB 评分排行",
            subtitle=f"{scope} · {label}",
        )
        yield await self.picture(data, layout=layout)
