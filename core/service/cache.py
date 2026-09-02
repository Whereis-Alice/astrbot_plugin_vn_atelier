"""图片缓存。

缓存策略与上游的三点差异：

1. 上游把 base64 字符串写进磁盘，同一张图占用约 4/3 的空间，而且每次读取
   都要再解码一次。这里统一存原始 JPEG 字节，需要 base64 时由调用方转。
2. 上游没有任何淘汰机制，缓存目录会无限膨胀。这里加了 TTL 与容量上限。
3. 新增一层内存 LRU，热点图片（占位图、同一批渲染里重复出现的封面）
   不必反复读盘。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..settings import Settings
from ..types import CacheGroup, NoCacheException
from ..utils import FileIO, to_jpeg_async

_LOG = "[视觉小说工房]"

_DIR_NAME = "image_cache"

_JPEG_SUFFIXES = frozenset({"jpg", "jpeg"})

_SECONDS_PER_DAY = 86400


class ImageCache:
    """磁盘 + 内存两级图片缓存。"""

    def __init__(self, settings: Settings, root: Path) -> None:
        self._settings = settings
        self._root = root / _DIR_NAME
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._lock = asyncio.Lock()

    # -- 生命周期 ---------------------------------------------------------- #

    async def initialize(self) -> None:
        """建目录，并按配置决定是清空还是只做一次淘汰。"""
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
        if self._settings.basic.clean_on_restart:
            await self.clear()
        else:
            await self.sweep()

    async def terminate(self) -> None:
        self._memory.clear()

    # -- 键与路径 ---------------------------------------------------------- #

    @staticmethod
    def _digest(url: str) -> str:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()

    def path_of(self, group: CacheGroup, url: str) -> Path:
        """URL 对应的缓存文件路径。

        上游直接把 URL 尾段当文件名，带查询串的地址会造出非法文件名，
        不同作品的 cover.jpg 还会互相覆盖。改用 sha1 摘要即可两全。
        """
        return self._root / group.value / f"{self._digest(url)}.jpg"

    def _memory_key(self, group: CacheGroup, url: str) -> str:
        return f"{group.value}/{self._digest(url)}"

    # -- 内存层 ------------------------------------------------------------ #

    def _memory_get(self, key: str) -> bytes | None:
        if self._settings.cache.memory_entries <= 0:
            return None
        data = self._memory.get(key)
        if data is not None:
            self._memory.move_to_end(key)
        return data

    def _memory_put(self, key: str, data: bytes) -> None:
        limit = self._settings.cache.memory_entries
        if limit <= 0:
            return
        self._memory[key] = data
        self._memory.move_to_end(key)
        while len(self._memory) > limit:
            self._memory.popitem(last=False)

    # -- 读写 -------------------------------------------------------------- #

    def _expired(self, path: Path) -> bool:
        ttl_days = self._settings.cache.ttl_days
        if ttl_days <= 0:
            return False
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return True
        return age > ttl_days * _SECONDS_PER_DAY

    async def read(self, group: CacheGroup, url: str) -> bytes | None:
        """读缓存。未命中或已过期返回 None。"""
        key = self._memory_key(group, url)
        cached = self._memory_get(key)
        if cached is not None:
            return cached

        path = self.path_of(group, url)
        if await asyncio.to_thread(self._expired, path):
            return None
        try:
            data = await FileIO.read_bytes(path)
        except NoCacheException:
            return None
        except OSError as error:
            logger.warning("%s 读取缓存失败 %s：%s", _LOG, path, error)
            return None
        if data:
            self._memory_put(key, data)
        return data or None

    async def write(self, group: CacheGroup, url: str, data: bytes) -> bytes:
        """写缓存，返回实际落盘的字节（可能被转码成 JPEG）。

        只有当源文件本来就不是 JPEG 时才转码，避免上游那种「每张图都
        重新编码一遍」的无谓开销与画质损失。
        """
        payload = data
        if FileIO.suffix_of_url(url) not in _JPEG_SUFFIXES:
            try:
                payload = await to_jpeg_async(data)
            except Exception as error:
                logger.warning("%s 图片转码失败，按原样缓存 %s：%s", _LOG, url, error)
                payload = data

        path = self.path_of(group, url)
        await FileIO.write_bytes(path, payload)
        self._memory_put(self._memory_key(group, url), payload)
        return payload

    # -- 维护 -------------------------------------------------------------- #

    def _all_files(self) -> list[Path]:
        if not self._root.is_dir():
            return []
        return [item for item in self._root.rglob("*.jpg") if item.is_file()]

    def _sweep_sync(self) -> tuple[int, int]:
        """删除过期文件，并在超出容量上限时按最后访问时间淘汰。"""
        removed = 0
        freed = 0
        entries: list[tuple[float, int, Path]] = []
        for path in self._all_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            if self._expired(path):
                try:
                    path.unlink()
                except OSError:
                    continue
                removed += 1
                freed += stat.st_size
                continue
            entries.append((stat.st_mtime, stat.st_size, path))

        max_bytes = self._settings.cache.max_size_mb * 1024 * 1024
        if max_bytes <= 0:
            return removed, freed

        total = sum(size for _, size, _ in entries)
        if total <= max_bytes:
            return removed, freed

        entries.sort(key=lambda item: item[0])
        for _, size, path in entries:
            if total <= max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
            freed += size
        return removed, freed

    async def sweep(self) -> tuple[int, int]:
        """异步执行一次淘汰，返回（删除文件数，释放字节数）。"""
        async with self._lock:
            removed, freed = await asyncio.to_thread(self._sweep_sync)
        if removed:
            logger.info(
                "%s 缓存清理完成：删除 %s 个文件，释放 %.1f MB",
                _LOG,
                removed,
                freed / 1024 / 1024,
            )
            self._memory.clear()
        return removed, freed

    def _stats_sync(self) -> dict[str, Any]:
        per_group: dict[str, dict[str, int]] = {}
        files = 0
        size = 0
        for path in self._all_files():
            try:
                length = path.stat().st_size
            except OSError:
                continue
            files += 1
            size += length
            bucket = per_group.setdefault(path.parent.name, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += length
        return {"files": files, "bytes": size, "groups": per_group}

    async def stats(self) -> dict[str, Any]:
        """统计缓存占用，附带内存层条数。"""
        data = await asyncio.to_thread(self._stats_sync)
        data["memory"] = len(self._memory)
        return data

    def _clear_sync(self) -> int:
        removed = 0
        for path in self._all_files():
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        return removed

    async def clear(self) -> int:
        """清空全部缓存，返回删除的文件数。"""
        async with self._lock:
            removed = await asyncio.to_thread(self._clear_sync)
        self._memory.clear()
        logger.info("%s 已清空图片缓存，共删除 %s 个文件", _LOG, removed)
        return removed
