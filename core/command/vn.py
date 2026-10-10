"""作品搜索：工房 作品 <关键词>。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..types import CommandType, Layout, Tips
from .base import BaseCommand, gallery


class VnCommand(BaseCommand):
    command = CommandType.VN

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        keyword = self.require(value)
        if self.settings.bangumi.enabled:
            try:
                items = await self.runtime.bangumi.search(keyword)
            except Tips:
                items = []
            if items:
                yield await self.picture(
                    gallery(
                        cards=await self.bangumi_cards(items),
                        title="作品检索 · Bangumi",
                        subtitle=keyword,
                    ),
                    layout=Layout.GALLERY,
                )
                return
        results = await self.runtime.vndb.request_by_vn(keyword)
        layout, data = await self.vn_view(results, title="作品检索 · VNDB", subtitle=keyword)
        yield await self.picture(data, layout=layout)
