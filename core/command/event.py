"""今日简讯：工房 简讯 [月-日]。

同一天里「发售纪念日的作品」与「生日的角色」各成一节，任一节为空就
整节跳过，不会留一个空标题在图上。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from astrbot.api.event import MessageEventResult

from ..types import JA_WEEKDAYS, CommandType, NoResultException
from .base import BaseCommand, dossier, section


class EventCommand(BaseCommand):
    command = CommandType.EVENT

    def _title(self, year: int, month: int, day: int) -> str:
        base = f"{year}年{month}月{day}日"
        try:
            weekday = datetime(year, month, day).weekday()
        except ValueError:
            # 2-29 在平年不存在，此时省略星期而不是让整条指令失败。
            return base
        return f"{base} · {JA_WEEKDAYS[weekday]}曜日"

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        now = datetime.now()
        if value.strip():
            month, day = self.split_date(value)
        else:
            month, day = now.month, now.day

        date = [str(now.year), f"{month:02d}", f"{day:02d}"]
        vns, characters = await self.runtime.vndb.request_by_event(date)
        if not vns and not characters:
            raise NoResultException(self.command, f"{month}-{day}")

        blocks = []
        if vns:
            blocks.append(
                section(
                    text=("今天是这些作品的发布纪念日",),
                    cards=await self.vn_cards(vns),
                )
            )
        if characters:
            blocks.append(
                section(
                    text=("今天是这些角色的生日",),
                    cards=await self.character_cards(characters),
                )
            )

        data = dossier(
            sections=blocks,
            title=self._title(now.year, month, day),
            subtitle="今日简讯",
        )
        yield await self.picture(data)
