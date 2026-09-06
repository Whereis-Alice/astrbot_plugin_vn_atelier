"""依赖装配容器。

上游把 HTTP 会话、配置、缓存目录等都放在模块级全局变量里，插件热重载
后旧对象不会被回收，同一进程装两次就会互相踩。这里把所有有状态对象收
进一个 AtelierRuntime 实例，生命周期与 Star 实例绑定：initialize() 建
立、terminate() 释放，指令层只拿到这一个对象。
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .network import AnimeTrace, Downloader, Http, TouchGal, Vndb
from .service import (
    Assets,
    Collection,
    Cooldown,
    ImageCache,
    KvStore,
    Renderer,
    Subscription,
)
from .settings import Settings
from .types import THEME_LABELS, Theme

_LOG = "[Gal工房]"


class AtelierRuntime:
    """一次插件加载对应一个运行时。"""

    def __init__(
        self,
        config: MutableMapping[str, Any] | None,
        store: KvStore,
        data_dir: Path,
        resources: Path,
    ) -> None:
        self.config = config
        self.data_dir = data_dir
        self.resources = resources
        self.settings = Settings.from_config(config)

        self.http = Http(self.settings)
        self.downloader = Downloader(self.settings)
        self.vndb = Vndb(self.http, self.settings)
        self.touchgal = TouchGal(self.http, self.settings)
        self.animetrace = AnimeTrace(self.http, self.settings)

        self.cache = ImageCache(self.settings, data_dir)
        self.assets = Assets(self.cache, self.downloader, resources)
        self.renderer = Renderer(self.settings, resources)
        self.cooldown = Cooldown(self.settings)
        self.subscription = Subscription(store, self.settings)
        self.collection = Collection(store)

    # -- 生命周期 ---------------------------------------------------------- #

    async def initialize(self) -> None:
        """建立网络会话与缓存目录。"""
        self.settings.log_warnings()
        await self.http.initialize()
        await self.downloader.initialize()
        await self.cache.initialize()

    async def terminate(self) -> None:
        """释放资源。任一环节失败都不影响其余环节。"""
        for name, target in (
            ("缓存", self.cache),
            ("下载器", self.downloader),
            ("HTTP 会话", self.http),
        ):
            try:
                await target.terminate()
            except Exception as error:
                logger.warning("%s 释放%s失败：%s", _LOG, name, error)

    # -- 运行期改配置 ------------------------------------------------------ #

    def persist_theme(self, theme: Theme) -> bool:
        """切换主题并尽力写回配置文件。

        返回是否成功持久化：失败时内存里的主题依然会生效，只是重启后
        会回到配置文件里的值，指令层据此给用户不同的提示。
        """
        self.settings.render.theme = theme
        self.renderer.invalidate()

        config = self.config
        if config is None:
            return False
        try:
            section = config.get("renderSetting")
            if not isinstance(section, MutableMapping):
                return False
            section["theme"] = f"{theme.value}-{THEME_LABELS.get(theme, theme.value)}"
            save = getattr(config, "save_config", None)
            if not callable(save):
                return False
            save()
        except Exception as error:
            logger.warning("%s 保存主题设置失败：%s", _LOG, error)
            return False
        return True
