"""持久化状态：推送订阅与收藏清单。

两者都放在 AstrBot 提供的插件 KV 存储里，不额外建数据库文件，卸载插件
时数据随之清理。KV 只支持 JSON 可序列化的值，因此收藏条目落盘时用
朴素的 dict，读出来再还原成模型。
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from astrbot.api import logger
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from pydantic import BaseModel, Field

from ..settings import Settings

_LOG = "[视觉小说工房]"

_PUSH_KEY = "push_sessions"
_COLLECTION_PREFIX = "collection:"

_MAX_COLLECTION = 100
"""单用户收藏上限，避免 KV 里存下无边界增长的列表。"""


class KvStore(Protocol):
    """插件 KV 存储的最小接口。

    只依赖协议而不是直接依赖 Star 实例，服务层就能在测试里换成内存实现。
    """

    async def put_kv_data(self, key: str, value: Any) -> None: ...

    async def get_kv_data(self, key: str, default: Any) -> Any: ...

    async def delete_kv_data(self, key: str) -> None: ...


# --------------------------------------------------------------------------- #
# 推送订阅
# --------------------------------------------------------------------------- #


class Subscription:
    """按会话维度管理每日推送订阅。

    上游只能在配置文件里手写「平台id-群号」白名单，改一次要重启插件。
    这里改成群内一条指令即可开关，配置里的白名单仍然生效，作为管理员
    统一下发的兜底名单。
    """

    def __init__(self, store: KvStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    async def all(self) -> list[str]:
        """已订阅的会话标识列表。"""
        raw = await self._store.get_kv_data(_PUSH_KEY, [])
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if str(item).strip()]

    async def contains(self, umo: str) -> bool:
        return umo in await self.all()

    async def add(self, umo: str) -> bool:
        """订阅。已订阅时返回 False。"""
        current = await self.all()
        if umo in current:
            return False
        current.append(umo)
        await self._store.put_kv_data(_PUSH_KEY, current)
        return True

    async def remove(self, umo: str) -> bool:
        """退订。原本未订阅时返回 False。"""
        current = await self.all()
        if umo not in current:
            return False
        current.remove(umo)
        await self._store.put_kv_data(_PUSH_KEY, current)
        return True

    def whitelist(self) -> list[str]:
        """把配置里的「平台id-群聊id」白名单转成会话标识。"""
        result: list[str] = []
        for platform, session in self._settings.schedule.push_list:
            try:
                result.append(
                    str(
                        MessageSession(
                            platform_name=platform,
                            message_type=MessageType.GROUP_MESSAGE,
                            session_id=session,
                        )
                    )
                )
            except (TypeError, ValueError) as error:
                logger.warning("%s 白名单项无法解析：%s-%s（%s）", _LOG, platform, session, error)
        return result

    async def targets(self) -> list[str]:
        """本次推送的全部目标会话，已去重。"""
        seen: set[str] = set()
        result: list[str] = []
        candidates = list(self.whitelist())
        if self._settings.schedule.use_subscription:
            candidates.extend(await self.all())
        for umo in candidates:
            if umo not in seen:
                seen.add(umo)
                result.append(umo)
        return result


# --------------------------------------------------------------------------- #
# 收藏清单
# --------------------------------------------------------------------------- #


class CollectionEntry(BaseModel):
    """一条收藏记录。"""

    id: str
    """VNDB ID，形如 v17 / c123 / p45。"""

    title: str = ""
    kind: str = "vn"
    """条目类型：vn / character / producer。"""

    added_at: float = Field(default_factory=time.time)

    @property
    def label(self) -> str:
        return self.title or self.id


class Collection:
    """每用户的「想玩 / 收藏」清单。"""

    def __init__(self, store: KvStore) -> None:
        self._store = store

    @staticmethod
    def _key(owner: str) -> str:
        return f"{_COLLECTION_PREFIX}{owner}"

    async def list(self, owner: str) -> list[CollectionEntry]:
        """按加入时间倒序返回收藏条目。"""
        raw = await self._store.get_kv_data(self._key(owner), [])
        if not isinstance(raw, list):
            return []
        entries: list[CollectionEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(CollectionEntry.model_validate(item))
            except ValueError:
                continue
        entries.sort(key=lambda entry: entry.added_at, reverse=True)
        return entries

    async def add(self, owner: str, entry: CollectionEntry) -> bool:
        """加入收藏。已存在或已达上限时返回 False。"""
        entries = await self.list(owner)
        if any(item.id == entry.id for item in entries):
            return False
        if len(entries) >= _MAX_COLLECTION:
            return False
        entries.append(entry)
        await self._save(owner, entries)
        return True

    async def remove(self, owner: str, entry_id: str) -> CollectionEntry | None:
        """移除收藏，返回被移除的条目；不存在时返回 None。"""
        entries = await self.list(owner)
        target = next((item for item in entries if item.id == entry_id), None)
        if target is None:
            return None
        await self._save(owner, [item for item in entries if item.id != entry_id])
        return target

    async def clear(self, owner: str) -> None:
        await self._store.delete_kv_data(self._key(owner))

    async def full(self, owner: str) -> bool:
        return len(await self.list(owner)) >= _MAX_COLLECTION

    async def _save(self, owner: str, entries: list[CollectionEntry]) -> None:
        await self._store.put_kv_data(self._key(owner), [entry.model_dump() for entry in entries])
