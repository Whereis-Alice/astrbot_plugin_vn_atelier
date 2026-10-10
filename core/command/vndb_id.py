"""VNDB ID 精确查询：工房 ID <v17 / c123 / p47>。

作品 ID 会额外去 TouchGal 找一份中文简介与预览图，命中时切换成特写
布局；找不到就安静退回画廊，不打扰用户。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from astrbot.api.event import MessageEventResult

from ..types import (
    ID_PREFIX_TO_COMMAND,
    ArgsOrNullException,
    Base64,
    CacheGroup,
    CommandType,
    NoResultException,
    SettingException,
    Tips,
)
from ..utils import paragraphs, parse_touchgal_details_async, plain_markup
from .base import BaseCommand


class VndbIdCommand(BaseCommand):
    command = CommandType.ID

    async def _touchgal_extra(self, vn_id: str) -> tuple[list[str], list[Base64]]:
        """按 VNDB ID 去 TouchGal 找简介与预览图。任何失败都返回空。"""
        try:
            results, _ = await self.runtime.touchgal.request_vn_by_search(CommandType.ID, vn_id)
            html = await self.runtime.touchgal.request_html(results[0].uniqueId)
            details = await parse_touchgal_details_async(html)
        except (NoResultException, SettingException, Tips, IndexError):
            return [], []

        desc = paragraphs(details.description)
        previews = await self.covers(details.previews, CacheGroup.TOUCHGAL)
        return desc, previews

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        raw = self.require(value).lower()
        real_type = ID_PREFIX_TO_COMMAND.get(raw[0])
        if real_type is None or not raw[1:].isdigit():
            raise ArgsOrNullException(self.command.value, raw)

        response = await self.runtime.vndb.request_by_id(real_type, raw)

        if real_type is CommandType.PRODUCER:
            producers, works = response
            layout, data = await self.producer_view(
                producers, works, title="厂商档案", subtitle=raw
            )
        elif real_type is CommandType.CHARACTER:
            layout, data = await self.character_view(response, title="角色档案", subtitle=raw)
        else:
            results: Sequence = response
            match = await self.runtime.bangumi.match_vn(results[0])
            if match.subject and match.subject.summary:
                desc, previews = paragraphs(plain_markup(match.subject.summary))[:12], []
            else:
                desc, previews = await self._touchgal_extra(raw)
            layout, data = await self.vn_view(
                results, desc=desc, previews=previews, title="作品档案", subtitle=raw
            )

        yield await self.picture(data, layout=layout)
