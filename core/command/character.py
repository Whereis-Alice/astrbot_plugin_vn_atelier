"""角色搜索：工房 角色 <关键词>。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..types import CommandType
from .base import BaseCommand


class CharacterCommand(BaseCommand):
    command = CommandType.CHARACTER

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        keyword = self.require(value)
        results = await self.runtime.vndb.request_by_character(keyword)
        layout, data = await self.character_view(results, title="角色检索", subtitle=keyword)
        yield await self.picture(data, layout=layout)
