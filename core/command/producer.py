"""厂商搜索：工房 厂商 <关键词>。

每个厂商单独一节，节内列出其代表作，数量由 producerVns 控制。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..types import CommandType
from .base import BaseCommand


class ProducerCommand(BaseCommand):
    command = CommandType.PRODUCER

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        keyword = self.require(value)
        producers, works = await self.runtime.vndb.request_by_producer(keyword)
        layout, data = await self.producer_view(
            producers,
            works,
            title="厂商检索",
            subtitle=keyword,
            notes=("作品按评分从高到低排列，数量可在插件配置中调整。",),
        )
        yield await self.picture(data, layout=layout)
