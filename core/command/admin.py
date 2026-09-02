"""管理员指令：缓存 / 主题 / 推送。

这三条都不参与冷却——运维操作被自己的限流挡住毫无意义。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, ClassVar

from astrbot.api.event import MessageEventResult

from ..types import THEME_LABELS, CommandType, Theme
from .base import BaseCommand, group, panel, row

_CLEAN_WORDS = frozenset({"清理", "清空", "clean", "clear"})

_ON_WORDS = frozenset({"开启", "打开", "订阅", "on"})
_OFF_WORDS = frozenset({"关闭", "取消", "退订", "off"})

_GROUP_NAMES = {"vndb": "VNDB 封面", "touchgal": "TouchGal 图片"}


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MB"


class CacheCommand(BaseCommand):
    """工房 缓存 [清理]。"""

    command = CommandType.HELP
    cooldown: ClassVar[bool] = False

    def _panel(self, stats: Mapping[str, Any], subtitle: str) -> dict:
        groups = [
            group(
                "总计",
                [
                    row("文件数", str(stats.get("files", 0))),
                    row("占用空间", _mb(int(stats.get("bytes", 0)))),
                    row("内存条目", str(stats.get("memory", 0))),
                ],
            )
        ]
        detail = stats.get("groups") or {}
        rows = [
            row(
                _GROUP_NAMES.get(name, name),
                f"{item.get('files', 0)} 个 · {_mb(int(item.get('bytes', 0)))}",
            )
            for name, item in detail.items()
        ]
        if rows:
            groups.append(group("分组", rows))

        limits = self.settings.cache
        groups.append(
            group(
                "上限",
                [
                    row("容量上限", f"{limits.max_size_mb} MB"),
                    row("过期天数", f"{limits.ttl_days} 天"),
                    row("内存条目上限", str(limits.memory_entries)),
                ],
            )
        )
        return panel(
            groups=groups,
            title="图片缓存",
            subtitle=subtitle,
            notes=("发送「工房 缓存 清理」立即按过期时间与容量上限清理一次。",),
        )

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        subtitle = "当前状态"
        if value.strip() in _CLEAN_WORDS:
            removed, freed = await self.runtime.cache.sweep()
            subtitle = f"已清理 {removed} 个文件 · 释放 {_mb(freed)}"
        stats = await self.runtime.cache.stats()
        yield await self.picture(self._panel(stats, subtitle))


class ThemeCommand(BaseCommand):
    """工房 主题 [名称]。"""

    command = CommandType.HELP
    cooldown: ClassVar[bool] = False

    @staticmethod
    def _match(value: str) -> Theme | None:
        raw = value.strip().lower()
        for theme, label in THEME_LABELS.items():
            if raw in {theme.value, label, label.lower()}:
                return theme
        return None

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        current = self.settings.render.theme
        if not value.strip():
            lines = [
                f"{'▸' if theme is current else '·'} {label}（{theme.value}）"
                for theme, label in THEME_LABELS.items()
            ]
            yield self.text("可用主题：\n" + "\n".join(lines) + "\n发送「工房 主题 樱色」切换。")
            return

        theme = self._match(value)
        if theme is None:
            names = " / ".join(THEME_LABELS.values())
            yield self.text(f"没有这个主题。可选：{names}")
            return

        saved = self.runtime.persist_theme(theme)
        label = THEME_LABELS[theme]
        if saved:
            yield self.text(f"已切换到「{label}」主题。")
        else:
            yield self.text(f"已临时切换到「{label}」主题，但写回配置失败，重启后会恢复原设置。")


class PushCommand(BaseCommand):
    """工房 推送 开启/关闭。"""

    command = CommandType.HELP
    cooldown: ClassVar[bool] = False

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        raw = value.strip()
        umo = self.event.unified_msg_origin
        schedule = self.settings.schedule

        if raw in _ON_WORDS:
            added = await self.runtime.subscription.add(umo)
            hint = (
                "" if schedule.use_subscription else "（注意：当前配置为按白名单推送，订阅不生效）"
            )
            yield self.text(
                f"已开启每日简讯推送，每天 {schedule.push_time} 送达。{hint}"
                if added
                else "本会话已经订阅过了。"
            )
            return

        if raw in _OFF_WORDS:
            removed = await self.runtime.subscription.remove(umo)
            yield self.text("已关闭每日简讯推送。" if removed else "本会话尚未订阅。")
            return

        subscribed = await self.runtime.subscription.contains(umo)
        mode = "订阅制" if schedule.use_subscription else "白名单"
        yield self.text(
            f"每日简讯：{'已订阅' if subscribed else '未订阅'}\n"
            f"推送时间：{schedule.push_time}\n"
            f"推送模式：{mode}\n"
            "发送「工房 推送 开启」或「工房 推送 关闭」调整。"
        )
