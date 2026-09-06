"""文件读写与 base64 转换。"""

from __future__ import annotations

import base64
import re
from pathlib import Path, PurePosixPath

import aiofiles
from astrbot.api import logger

from ..types import MIME_TYPES, Base64, NoCacheException

_DATA_URI = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


class FileIO:
    """薄封装的异步文件读写。

    与上游相比的两点修正：写文本必须显式指定 utf-8（否则在中文 Windows
    上会按 gbk 落盘），以及允许覆盖已存在的文件（上游遇到同名文件直接
    静默返回 False，缓存永远刷新不了）。
    """

    @staticmethod
    async def read_bytes(path: Path) -> bytes:
        """读二进制文件。文件不存在时抛 NoCacheException。"""
        if not path.is_file():
            raise NoCacheException(str(path))
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    @staticmethod
    async def write_bytes(path: Path, data: bytes) -> bool:
        """写二进制文件，自动建父目录。返回是否成功。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, "wb") as handle:
                await handle.write(data)
        except OSError as error:
            logger.warning("[Gal工房] 写入文件失败 %s：%s", path, error)
            return False
        return True

    @staticmethod
    async def read_text(path: Path, encoding: str = "utf-8") -> str:
        """读文本文件。文件不存在时抛 NoCacheException。"""
        if not path.is_file():
            raise NoCacheException(str(path))
        async with aiofiles.open(path, encoding=encoding) as handle:
            return await handle.read()

    @staticmethod
    def read_text_sync(path: Path, encoding: str = "utf-8") -> str:
        """同步读文本。只在插件加载阶段读取模板资源时使用。"""
        if not path.is_file():
            raise NoCacheException(str(path))
        return path.read_text(encoding=encoding)

    @staticmethod
    async def write_text(path: Path, text: str, encoding: str = "utf-8") -> bool:
        """写文本文件，覆盖同名文件。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(path, "w", encoding=encoding) as handle:
                await handle.write(text)
        except OSError as error:
            logger.warning("[Gal工房] 写入文件失败 %s：%s", path, error)
            return False
        return True

    @staticmethod
    def to_base64(data: bytes, prefix: bool = True, suffix: str = "jpg") -> Base64:
        """把二进制转成 base64 字符串，可选择带上 data URI 前缀。"""
        encoded = base64.b64encode(data).decode("utf-8")
        if not prefix:
            return encoded
        mime = MIME_TYPES.get(suffix.lower().lstrip("."), "image/jpeg")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def strip_prefix(text: Base64) -> str:
        """去掉 data URI 前缀，只留 base64 主体。"""
        return _DATA_URI.sub("", text.strip())

    @staticmethod
    def from_base64(text: Base64) -> bytes:
        """把 base64 字符串还原成二进制。"""
        return base64.b64decode(FileIO.strip_prefix(text))

    @staticmethod
    def suffix_of_url(url: str, default: str = "jpg") -> str:
        """从 URL 路径推断文件后缀。

        上游用 rsplit(".", 1) 直接切 URL，遇到不带扩展名或带查询串的
        地址就会算错甚至抛 ValueError，进而导致「已经是 jpeg 的图片
        每次都被重新编码」。这里只看 URL 的 path 部分。
        """
        try:
            from urllib.parse import urlparse

            path = PurePosixPath(urlparse(url).path)
        except (ValueError, TypeError):
            return default
        suffix = path.suffix.lower().lstrip(".")
        return suffix if suffix in MIME_TYPES else default
