"""把 API 响应拼装成「字段：值」形式的中文信息行。

这些函数只输出**纯文本**列表，不含任何 HTML。转义与换行由渲染层
（core.service.renderer）统一负责，命令层永远不用关心 HTML 安全。

上游用一个带 class 级字段的 Splicer 对象承担同样的职责，多个实例会
互相污染，而且空值判定用的是 falsy，导致「评分 0」「身高 0」这类
合法数值被当成空值丢掉。这里改成一组无状态函数，两个问题都不复存在。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sized
from typing import Any

from ..types import (
    DEVELOPER_TYPES,
    GENDERS,
    LANG_NAMES,
    ResourceResponse,
    TouchGalResponse,
    VndbCharacterResponse,
    VndbProducerResponse,
    VndbVnResponse,
)
from .text import join_cn

_UNKNOWN = "??"


def _blank(value: Any) -> bool:
    """空值判定。注意数值 0 不算空，这是与上游行为的关键差异。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Sized) and not isinstance(value, str):
        return len(value) == 0
    return False


class _Lines:
    """顺序收集信息行的小工具，自动跳过空值。"""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: list[str] = []

    def add(self, label: str, value: Any) -> _Lines:
        """value 非空时追加一行「label：value」。"""
        if not _blank(value):
            self._items.append(f"{label}：{value}")
        return self

    def add_raw(self, line: str) -> _Lines:
        if not _blank(line):
            self._items.append(line)
        return self

    def extend_raw(self, lines: Iterable[str]) -> _Lines:
        for line in lines:
            self.add_raw(line)
        return self

    def build(self) -> list[str]:
        return self._items


def _lang_name(code: str | None) -> str:
    return LANG_NAMES.get(code or "", "")


def _sex_at(sex: list[str | None] | None, index: int) -> str:
    """安全读取 VNDB 的 sex 数组：[外观性别, 真实性别]，元素可能为 null。"""
    if not sex or index >= len(sex):
        return ""
    return GENDERS.get(sex[index] or "", "")


# --------------------------------------------------------------------------- #
# VNDB
# --------------------------------------------------------------------------- #


def vn_lines(vn: VndbVnResponse) -> list[str]:
    """作品信息行。"""
    lines = _Lines().add("VNDB ID", vn.id)

    for title in vn.titles or []:
        name = _lang_name(title.lang)
        if name:
            official = "官方" if title.official else "非官方"
            lines.add_raw(f"{name}标题（{official}）：{title.title}")

    lines.add("别名", join_cn(vn.aliases))
    if vn.rating is not None:
        valid = math.isfinite(vn.rating) and 0 < vn.rating <= 100 and vn.votecount != 0
        lines.add("VNDB 贝叶斯评分（100分制）", vn.rating if valid else "暂无")
    lines.add("VNDB 平均分（100分制）", vn.average)
    lines.add("VNDB 评价人数", vn.votecount)
    if vn.length_minutes:
        lines.add("游玩时间", f"{round(vn.length_minutes / 60, 1)}小时")
    developers = [f"{d.original or d.name}（{d.id}）" for d in vn.developers or []]
    lines.add("制作者（VNDB ID）", join_cn(developers))
    lines.add("发布日期", vn.released)
    lines.add("支持平台", join_cn(vn.platforms))
    return lines.build()


def character_lines(
    character: VndbCharacterResponse,
    *,
    options: frozenset[str] = frozenset(),
    ignore_extra: bool = False,
    ignore_vns: bool = False,
) -> list[str]:
    """角色信息行。

    Args:
        character: VNDB 角色响应。
        options: 已启用的额外信息键，见 CHARACTER_OPTION_KEYS 的值域。
        ignore_extra: 忽略全部额外信息（出处识别的小卡片用）。
        ignore_vns: 不列出出场作品（避免与卡片标题重复）。
    """
    lines = _Lines().add("VNDB ID", character.id)
    lines.add("别称", join_cn(character.aliases))

    if character.birthday and len(character.birthday) >= 2:
        lines.add_raw(f"生日：{character.birthday[0]}月{character.birthday[1]}日")

    if not ignore_vns:
        works = [f"「{vn.alttitle or vn.title}」（{vn.id}）" for vn in character.vns or []]
        lines.add("出场作品（VNDB ID）", join_cn(works))

    if ignore_extra or not options:
        return lines.build()

    if "blood" in options:
        lines.add("血型", character.blood_type)
    if "measure" in options and not (character.height is None and character.weight is None):
        height = character.height if character.height is not None else _UNKNOWN
        weight = character.weight if character.weight is not None else _UNKNOWN
        lines.add_raw(f"身高/体重（cm/kg）：{height}/{weight}")
    if "gender_outer" in options:
        lines.add("性别", _sex_at(character.sex, 0))
    if "gender_inner" in options:
        lines.add("真实性别", _sex_at(character.sex, 1))
    if "bwh" in options and not (
        character.bust is None and character.waist is None and character.hips is None
    ):
        bust = character.bust if character.bust is not None else _UNKNOWN
        waist = character.waist if character.waist is not None else _UNKNOWN
        hips = character.hips if character.hips is not None else _UNKNOWN
        lines.add_raw(f"三围：{bust}-{waist}-{hips}")
    if "cup" in options:
        lines.add("罩杯", character.cup)
    return lines.build()


def producer_lines(producer: VndbProducerResponse, *, ignore_name: bool = False) -> list[str]:
    """厂商信息行。"""
    lines = _Lines()
    if not ignore_name:
        lines.add_raw(producer.display_name)
    lines.add("VNDB ID", producer.id)
    lines.add("别称", join_cn(producer.aliases))
    lines.add("文本语言", _lang_name(producer.lang))
    lines.add("类型", DEVELOPER_TYPES.get(producer.type or "", ""))
    return lines.build()


# --------------------------------------------------------------------------- #
# TouchGal
# --------------------------------------------------------------------------- #


def search_lines(game: TouchGalResponse, *, ignore_name: bool = False) -> list[str]:
    """TouchGal 搜索结果信息行。"""
    lines = _Lines().add("TouchGal ID", game.id)
    if not ignore_name:
        lines.add("游戏名", game.name)
    lines.add("站内评分", game.averageRating)
    languages = [_lang_name(code) for code in game.language]
    lines.add("资源语言", join_cn(languages))
    lines.add("资源属性", join_cn(game.type))
    lines.add("资源平台", join_cn(game.platform))
    return lines.build()


def download_lines(resource: ResourceResponse) -> list[str]:
    """单条下载资源的信息行，最后一行是链接明细块。"""
    lines = _Lines()
    lines.add("标题", resource.name)
    lines.add("类型", resource.section)
    lines.add("资源平台", join_cn(resource.platform))
    languages = [_lang_name(code) for code in resource.language]
    lines.add("资源语言", join_cn(languages))
    lines.add("备注", resource.note)

    blocks: list[str] = []
    for link in resource.links:
        parts = [
            "-" * 10,
            f"资源平台：{link.storage}" if link.storage else "",
            f"文件大小：{link.size}" if link.size else "",
            f"链接：{link.content}" if link.content else "",
            f"提取码：{link.code}" if link.code else "",
            f"解压码：{link.password}" if link.password else "",
        ]
        blocks.append("\n".join(part for part in parts if part))
    lines.add_raw("\n".join(blocks))
    return lines.build()
