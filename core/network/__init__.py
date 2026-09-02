"""外部 API 客户端。

统一约定：网络层只负责「请求 - 校验 - 转模型」，不做缓存、不做渲染、
不关心指令语义；失败一律抛 core.types 里的 Tips 子类。
"""

from .animetrace import AnimeTrace
from .downloader import Downloader
from .http import Http
from .touchgal import TouchGal
from .vndb import Vndb

__all__ = [
    "AnimeTrace",
    "Downloader",
    "Http",
    "TouchGal",
    "Vndb",
]
