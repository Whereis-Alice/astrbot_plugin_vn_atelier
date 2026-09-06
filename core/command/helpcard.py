"""帮助卡：工房 帮助。

指令表写在这里而不是散落在各处，新增指令时只改一个地方。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from astrbot.api.event import MessageEventResult

from ..types import THEME_LABELS, CommandType
from .base import BaseCommand, group, panel, row

_SEARCH = (
    ("工房 作品 <关键词>", "按名称检索视觉小说，附评分、平台与制作方"),
    ("工房 角色 <关键词>", "按名称检索角色档案"),
    ("工房 厂商 <关键词>", "检索会社，并列出其代表作"),
    ("工房 ID <v17/c123/p45>", "用 VNDB ID 精确查询，作品会附中文简介"),
    ("工房 排行 [年份] [标签]", "按 VNDB 评分出榜，可只看某年或某标签"),
)

_DISCOVER = (
    ("工房 随机", "随机抽一部作品，附简介与游戏截图"),
    ("工房 推荐 <标签...>", "按标签连续推荐，回「换一个」继续"),
    ("工房 简讯 [月-日]", "当天有哪些作品发售纪念日、哪些角色生日"),
    ("工房 出处 [图片]", "按图识别角色出处，支持直接发图或引用图片"),
    ("工房 下载 <ID/关键词>", "查询 TouchGal 上的资源链接"),
)

_PERSONAL = (
    ("工房 收藏 <VNDB ID>", "加入个人收藏夹"),
    ("工房 取消收藏 <VNDB ID>", "从收藏夹移除"),
    ("工房 清单", "查看个人收藏夹"),
)

_ADMIN = (
    ("工房 主题 [名称]", "切换卡片配色并写回配置"),
    ("工房 缓存 [清理]", "查看或清理图片缓存"),
    ("工房 推送 开启/关闭", "订阅或退订本会话的每日简讯"),
)


class HelpCommand(BaseCommand):
    command = CommandType.HELP

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        themes = " / ".join(THEME_LABELS.values())
        data = panel(
            groups=[
                group("检索", [row(name, desc) for name, desc in _SEARCH]),
                group("发现", [row(name, desc) for name, desc in _DISCOVER]),
                group("个人", [row(name, desc) for name, desc in _PERSONAL]),
                group("管理员", [row(name, desc) for name, desc in _ADMIN]),
            ],
            title="Gal工房",
            subtitle="主指令 工房，也可用 vna / gal工房",
            notes=(
                f"可选主题：{themes}",
                "数据来源：VNDB（元数据）· TouchGal（简介与资源）· AnimeTrace（识图）",
                "方括号表示可省略参数，尖括号表示必填参数。",
            ),
        )
        yield await self.picture(data)
