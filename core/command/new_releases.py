"""手动预览今日新作，不依赖是否启用定时新作推送。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from astrbot.api.event import MessageEventResult

from ..types import CommandType
from .base import BaseCommand
from .calendar import CalendarComposer


class NewReleasesCommand(BaseCommand):
    command = CommandType.NEW_RELEASES

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        now = datetime.now()
        date = [str(now.year), f"{now.month:02d}", f"{now.day:02d}"]
        data = await CalendarComposer(self.runtime).new_release_data(
            date, now.strftime("%Y年%m月%d日")
        )
        if data is None:
            yield self.text("VNDB 暂未返回今天首次发售的视觉小说条目；不代表今天一定没有新作。")
            return
        yield await self.picture(data)
