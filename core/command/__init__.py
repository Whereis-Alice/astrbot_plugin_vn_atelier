"""指令层：每个模块对应一条子指令。

main.py 只负责把 AstrBot 的指令装饰器映射到这里的处理器类，
所以新增指令只需：新建一个文件 → 在本文件导出 → 在 main.py 里
加一个子指令。
"""

from __future__ import annotations

from .admin import CacheCommand, PushCommand, ThemeCommand
from .base import (
    BaseCommand,
    Composer,
    card,
    dossier,
    feature,
    gallery,
    group,
    panel,
    row,
    section,
)
from .character import CharacterCommand
from .collection import CollectAddCommand, CollectListCommand, CollectRemoveCommand
from .daily import DailyDigest
from .download import DownloadCommand
from .event import EventCommand
from .find import FindCommand
from .helpcard import HelpCommand
from .producer import ProducerCommand
from .random_vn import RandomCommand
from .ranking import RankingCommand
from .recommend import RecommendCommand
from .vn import VnCommand
from .vndb_id import VndbIdCommand

__all__ = [
    "BaseCommand",
    "CacheCommand",
    "CharacterCommand",
    "CollectAddCommand",
    "CollectListCommand",
    "CollectRemoveCommand",
    "Composer",
    "DailyDigest",
    "DownloadCommand",
    "EventCommand",
    "FindCommand",
    "HelpCommand",
    "ProducerCommand",
    "PushCommand",
    "RandomCommand",
    "RankingCommand",
    "RecommendCommand",
    "ThemeCommand",
    "VnCommand",
    "VndbIdCommand",
    "card",
    "dossier",
    "feature",
    "gallery",
    "group",
    "panel",
    "row",
    "section",
]
