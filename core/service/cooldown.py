"""每用户冷却。

上游对所有指令毫无频率限制，一个人连发十条「推荐」就能把 VNDB 和
TouchGal 的请求配额打满，甚至触发对方风控。这里做最轻量的令牌控制：
同一用户在冷却窗口内的第二次调用直接被拒，不占用任何网络请求。
"""

from __future__ import annotations

import time

from ..settings import Settings
from ..types import CooldownException

_MAX_ENTRIES = 512
"""记录上限。超过后清掉已经不在冷却窗口里的键，避免长期运行内存缓慢增长。"""


class Cooldown:
    """基于「最近一次通过时间」的冷却检查。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._last: dict[str, float] = {}

    def check(self, key: str) -> None:
        """检查并占用一次配额。仍在冷却中时抛 CooldownException。"""
        window = self._settings.limit.cooldown_seconds
        if window <= 0:
            return

        now = time.monotonic()
        previous = self._last.get(key)
        if previous is not None and now - previous < window:
            raise CooldownException(max(1, int(window - (now - previous)) + 1))

        self._last[key] = now
        if len(self._last) > _MAX_ENTRIES:
            self._prune(now, window)

    def _prune(self, now: float, window: int) -> None:
        stale = [key for key, stamp in self._last.items() if now - stamp >= window]
        for key in stale:
            self._last.pop(key, None)

    def reset(self, key: str | None = None) -> None:
        """清除冷却记录。key 为 None 时清除全部。"""
        if key is None:
            self._last.clear()
        else:
            self._last.pop(key, None)
