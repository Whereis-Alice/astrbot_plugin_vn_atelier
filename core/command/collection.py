"""收藏清单：工房 收藏 / 取消收藏 / 清单。

上游没有这个功能。收藏项按 VNDB ID 存储，加入时顺便去 VNDB 取一次
标题，这样清单里显示的是作品名而不是一串 ID。数据存在 AstrBot 的
KV 存储里，按「平台:用户」隔离，群成员之间互不干扰。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from astrbot.api.event import MessageEventResult

from ..service import CollectionEntry
from ..types import (
    ID_PREFIX_TO_COMMAND,
    ArgsOrNullException,
    CommandType,
    Tips,
)
from .base import BaseCommand, group, panel, row

_KIND_LABELS = {
    "vn": "作品",
    "character": "角色",
    "producer": "厂商",
}

_MAX_ENTRIES = 100

_CLEAR_WORDS = frozenset({"清空", "清理", "clear"})


class _CollectionBase(BaseCommand):
    command = CommandType.COLLECTION

    @property
    def owner(self) -> str:
        """收藏的归属键：同一个人在不同平台各有一份清单。"""
        return f"{self.event.get_platform_name()}:{self.event.get_sender_id()}"

    def parse_id(self, value: str) -> tuple[str, CommandType]:
        raw = self.require(value).lower()
        real_type = ID_PREFIX_TO_COMMAND.get(raw[0])
        if real_type is None or not raw[1:].isdigit():
            raise ArgsOrNullException(self.command.value, raw)
        return raw, real_type


class CollectAddCommand(_CollectionBase):
    """工房 收藏 <VNDB ID>。"""

    async def _title_of(self, vnbd_id: str, real_type: CommandType) -> str:
        """取条目标题，失败时留空由 label 回退到 ID。"""
        try:
            response = await self.runtime.vndb.request_by_id(real_type, vnbd_id)
        except Tips:
            return ""
        items = response[0] if real_type is CommandType.PRODUCER else response
        if not items:
            return ""
        first = items[0]
        return str(
            getattr(first, "display_title", None) or getattr(first, "display_name", None) or ""
        )

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        vndb_id, real_type = self.parse_id(value)
        collection = self.runtime.collection

        if await collection.full(self.owner):
            yield self.text(f"收藏夹已满（上限 {_MAX_ENTRIES} 项），请先取消一些收藏。")
            return

        title = await self._title_of(vndb_id, real_type)
        entry = CollectionEntry(id=vndb_id, title=title, kind=real_type.value)
        added = await collection.add(self.owner, entry)
        if not added:
            yield self.text(f"「{entry.label}」已经在收藏夹里了。")
            return
        yield self.text(f"已收藏「{entry.label}」，发送「工房 清单」查看全部收藏。")


class CollectRemoveCommand(_CollectionBase):
    """工房 取消收藏 <VNDB ID>。"""

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        vndb_id, _ = self.parse_id(value)
        removed = await self.runtime.collection.remove(self.owner, vndb_id)
        if removed is None:
            yield self.text(f"收藏夹里没有 {vndb_id}。")
            return
        yield self.text(f"已移除「{removed.label}」。")


class CollectListCommand(_CollectionBase):
    """工房 清单。"""

    @staticmethod
    def _groups(entries: Sequence[CollectionEntry]) -> list[dict]:
        blocks: list[dict] = []
        for kind, name in _KIND_LABELS.items():
            rows = [row(entry.id, entry.label) for entry in entries if entry.kind == kind]
            if rows:
                blocks.append(group(name, rows))
        return blocks

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        if value.strip() in _CLEAR_WORDS:
            removed = await self.runtime.collection.clear(self.owner)
            yield self.text(
                f"已清空收藏夹，共移除 {removed} 项。" if removed else "收藏夹本来就是空的。"
            )
            return

        entries = await self.runtime.collection.list(self.owner)
        if not entries:
            yield self.text("收藏夹还是空的。用「工房 收藏 v17」把感兴趣的条目加进来。")
            return

        data = panel(
            groups=self._groups(entries),
            title="我的收藏",
            subtitle=f"共 {len(entries)} 项 · 按加入时间倒序",
            notes=(
                "工房 收藏 <VNDB ID> —— 加入收藏",
                "工房 取消收藏 <VNDB ID> —— 移出收藏",
                "工房 ID <VNDB ID> —— 查看条目详情",
                "工房 清单 清空 —— 清空整份收藏夹",
            ),
        )
        yield await self.picture(data)
