"""作品搜索：工房 作品 <关键词>。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..types import CommandType
from .base import BaseCommand


class VnCommand(BaseCommand):
    command = CommandType.VN

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        keyword = self.require(value)
        results = await self.runtime.vndb.request_by_vn(keyword)
        layout, data = await self.vn_view(results, title="作品检索", subtitle=keyword)
        yield await self.picture(data, layout=layout)
