"""图片下载器。

单独于 Http 存在，原因是图片下载需要不同的连接池策略（限制单主机并发、
复用长连接），而且失败时用「返回 None」表达，由调用方决定用占位图还是
报错，不适合走统一的异常路径。
"""

from __future__ import annotations

import asyncio

import aiohttp
from astrbot.api import logger

from ..settings import Settings

_LOG = "[Gal工房]"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class Downloader:
    """带指数退避的图片下载器。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """在事件循环内建立连接池。

        上游把 TCPConnector 建在模块导入期，插件热重载后 connector 依旧
        绑定在已关闭的事件循环上，所有下载都会直接失败。
        """
        await self._ensure_session()

    async def terminate(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit_per_host=5,
                    limit=20,
                    ttl_dns_cache=300,
                    keepalive_timeout=10,
                )
                timeout = aiohttp.ClientTimeout(total=self._settings.basic.request_timeout)
                self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    @property
    def proxy(self) -> str | None:
        return self._settings.safety.proxy or None

    async def download(self, url: str) -> bytes | None:
        """下载图片，失败返回 None。"""
        if not url.lower().startswith(("http://", "https://")):
            logger.warning("%s 跳过非法图片地址：%s", _LOG, url)
            return None

        retries = self._settings.basic.request_retries
        for attempt in range(1, retries + 1):
            try:
                session = await self._ensure_session()
                async with session.get(
                    url,
                    headers={"user-agent": _BROWSER_UA},
                    proxy=self.proxy,
                ) as response:
                    if response.status < 400:
                        return await response.read()
                    logger.warning("%s 下载图片 %s 返回 %s", _LOG, url, response.status)
                    if response.status < 500:
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                logger.warning(
                    "%s 下载图片失败 %s（第 %s/%s 次）：%s",
                    _LOG,
                    url,
                    attempt,
                    retries,
                    error,
                )
            if attempt < retries:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        return None
