"""图片素材获取。

把「缓存命中 → 下载 → 回写缓存 → 失败退占位图」这条链路收敛到一处。
上游把这段逻辑散落在每个指令里，任何一处忘记 try 就会让整条指令因为
一张封面 404 而失败；这里统一保证 **永远返回一个可用的 base64 图片**。
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..network import Downloader
from ..types import Base64, CacheGroup
from ..utils import FileIO
from .cache import ImageCache

_LOG = "[视觉小说工房]"

_ERROR_IMAGE = ("image", "error.jpg")

# 连 error.jpg 都缺失时用 PIL 现画一张，尺寸与封面卡片一致。
_PLACEHOLDER_SIZE = (300, 420)
_PLACEHOLDER_COLOR = (26, 28, 46)


class Assets:
    """封面 / 预览图的统一入口。"""

    def __init__(self, cache: ImageCache, downloader: Downloader, resources: Path) -> None:
        self._cache = cache
        self._downloader = downloader
        self._resources = resources
        self._error_raw: bytes | None = None
        self._lock = asyncio.Lock()

    # -- 占位图 ------------------------------------------------------------ #

    @staticmethod
    def _draw_placeholder() -> bytes:
        """error.jpg 缺失时的最后兜底：一张纯色 JPEG。"""
        try:
            from PIL import Image

            buffer = io.BytesIO()
            Image.new("RGB", _PLACEHOLDER_SIZE, _PLACEHOLDER_COLOR).save(
                buffer, format="JPEG", quality=80
            )
            return buffer.getvalue()
        except Exception as error:
            logger.warning("%s 生成占位图失败：%s", _LOG, error)
            return b""

    async def _error_bytes(self) -> bytes:
        if self._error_raw is not None:
            return self._error_raw
        async with self._lock:
            if self._error_raw is not None:
                return self._error_raw
            path = self._resources.joinpath(*_ERROR_IMAGE)
            data = b""
            try:
                data = await FileIO.read_bytes(path)
            except Exception as error:
                logger.warning("%s 读取占位图 %s 失败：%s", _LOG, path, error)
            self._error_raw = data or self._draw_placeholder()
            return self._error_raw

    async def error_image(self, *, prefix: bool = True) -> Base64:
        """占位图的 base64。"""
        return FileIO.to_base64(await self._error_bytes(), prefix=prefix)

    # -- 获取 -------------------------------------------------------------- #

    async def fetch(self, group: CacheGroup, url: str, *, prefix: bool = True) -> Base64:
        """按 URL 取图，失败时退回占位图。"""
        if not url:
            return await self.error_image(prefix=prefix)

        try:
            cached = await self._cache.read(group, url)
            if cached:
                return FileIO.to_base64(cached, prefix=prefix)
        except Exception as error:
            logger.warning("%s 读取缓存失败（%s）：%s", _LOG, url, error)

        try:
            raw = await self._downloader.download(url)
        except Exception as error:
            logger.warning("%s 下载图片失败（%s）：%s", _LOG, url, error)
            raw = None
        if not raw:
            return await self.error_image(prefix=prefix)

        try:
            stored = await self._cache.write(group, url, raw)
        except Exception as error:
            logger.warning("%s 写入缓存失败（%s）：%s", _LOG, url, error)
            stored = raw
        return FileIO.to_base64(stored or raw, prefix=prefix)

    async def batch(
        self, urls: Iterable[str], group: CacheGroup, *, prefix: bool = True
    ) -> list[Base64]:
        """并发取一批图，顺序与入参一致。"""
        targets = list(urls)
        if not targets:
            return []
        return list(
            await asyncio.gather(*(self.fetch(group, url, prefix=prefix) for url in targets))
        )

    async def vndb_covers(self, responses: Sequence[Any], *, prefix: bool = True) -> list[Base64]:
        """从 VNDB 响应里取封面。image 为空的条目自动用占位图。"""
        urls: list[str] = []
        for item in responses:
            image = getattr(item, "image", None)
            urls.append(str(getattr(image, "url", "") or "") if image else "")
        return await self.batch(urls, CacheGroup.VNDB, prefix=prefix)
