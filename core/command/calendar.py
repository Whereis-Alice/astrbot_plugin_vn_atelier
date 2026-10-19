"""每日清单与新作卡片，和每日精选分别组图、分别发送。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from astrbot.api import logger

from ..service.bangumi import BangumiMatch
from ..types import CacheGroup, VndbCharacterResponse, VndbVnResponse
from ..utils import brief, character_lines, paragraphs, plain_markup
from ..utils.bangumi import bangumi_lines, match_lines, rating_data
from ..utils.text import clip
from .base import Composer, card, dossier, section


class CalendarComposer(Composer):
    """少量并发补充资料，失败保留 VNDB；不需要浏览器或额外常驻服务。"""

    def vndb_picture(self, item: VndbVnResponse | VndbCharacterResponse) -> str:
        image = item.image
        if image is None:
            return ""
        if not self.settings.safety.enable_nsfw and image.sexual != 0:
            return ""
        return image.url

    async def work_matches(self, works: Sequence[VndbVnResponse]) -> dict[str, BangumiMatch]:
        semaphore = asyncio.Semaphore(2)

        async def one(work: VndbVnResponse) -> tuple[str, BangumiMatch]:
            async with semaphore:
                return work.id, await self.runtime.bangumi.match_vn(work)

        unique = {work.id: work for work in works}
        return dict(await asyncio.gather(*(one(work) for work in unique.values())))

    async def daily_work_cards(
        self, works: Sequence[VndbVnResponse], *, year: int, new: bool = False
    ) -> list[dict[str, Any]]:
        matches = await self.work_matches(works)

        async def one(work: VndbVnResponse) -> dict[str, Any]:
            match = matches[work.id]
            item = match.subject
            title = item.display_title if item else work.display_title
            image = await self.cover(
                item.cover if item and item.cover else self.vndb_picture(work),
                CacheGroup.BANGUMI if item and item.cover else CacheGroup.VNDB,
            )
            lines = [f"VNDB 首发：{work.released or '未知'}"]
            if item:
                lines.extend(
                    line
                    for line in bangumi_lines(item, compact=not new)
                    if not line.startswith(("Bangumi 评分：", "Bangumi 游戏排名：", "Bangumi ID："))
                    and line != item.url
                    and not (line.startswith("Bangumi 发售日：") and item.date == work.released)
                )
            else:
                lines.extend(match_lines(match))
                if work.alttitle and work.title != work.alttitle:
                    lines.append(f"原名 / 罗马字：{work.title}")
            if not item or not item.info("开发", "开发商"):
                developers = "、".join(d.original or d.name for d in work.developers or [])
                if developers:
                    lines.append(f"开发：{developers}")
            if (not item or not item.info("平台")) and work.platforms:
                lines.append(f"平台：{'、'.join(work.platforms)}")
            lines.append(f"来源：VNDB {work.id}" + (f" · Bangumi {item.id}" if item else ""))
            if new:
                summary = plain_markup(item.summary if item and item.summary else work.description)
                lines.extend(paragraphs(clip(summary, 600)) or ["暂无简介。"])
                lines.append(f"https://vndb.org/{work.id}")
                if item:
                    lines.append(item.url)
            age = year - int(work.released[:4]) if work.released else 0
            ratings = rating_data(item, work)
            if not ratings:
                ratings = [
                    {
                        "source": "VNDB",
                        "score": "暂无",
                        "scale": "",
                        "meta": "尚无有效评分",
                        "rank": "未评分",
                    }
                ]
            return card(
                image=image,
                subtitle=title,
                desc=lines,
                badge="今日首发条目" if new else f"发布 {age} 周年",
                ratings=ratings,
            )

        return list(await asyncio.gather(*(one(work) for work in works)))

    async def birthday_cards(
        self,
        characters: Sequence[VndbCharacterResponse],
        *,
        works: Sequence[VndbVnResponse] = (),
    ) -> list[dict[str, Any]]:
        # VNDB 的角色作品引用足以进行保守匹配，无需逐个再查 VNDB 详情。
        related = {work.id: work for work in works}
        for character in characters:
            for ref in character.vns or []:
                if ref.id not in related and ref.title:
                    related[ref.id] = VndbVnResponse.model_validate(ref.model_dump())
        matches = await self.work_matches(list(related.values()))
        semaphore = asyncio.Semaphore(2)

        async def one(character: VndbCharacterResponse) -> dict[str, Any]:
            refs = character.vns or []
            subjects = [
                matches[ref.id].subject
                for ref in refs
                if ref.id in matches and matches[ref.id].subject
            ]
            async with semaphore:
                bgm = await self.runtime.bangumi.match_character(character, subjects)
            image = await self.cover(
                bgm.cover if bgm and bgm.cover else self.vndb_picture(character),
                CacheGroup.BANGUMI if bgm and bgm.cover else CacheGroup.VNDB,
            )
            lines = character_lines(
                character, options=self.settings.character.options, ignore_vns=True
            )
            # 清单只保留角色资料与来源作品，不在角色栏目塞作品简介或评分。
            names = []
            for ref in refs:
                subject = matches.get(ref.id, BangumiMatch()).subject
                names.append(
                    f"{subject.display_title if subject else ref.alttitle or ref.title or ref.id}（{ref.id}）"
                )
            lines.append("来源作品：" + ("、".join(names) if names else "暂无关联作品"))
            if bgm:
                if bgm.display_name != character.display_name:
                    lines.insert(0, f"原名：{character.display_name}")
                lines.append(f"Bangumi 角色：{bgm.id}（已核对来源作品与姓名）")
            return card(
                image=image,
                subtitle=bgm.display_name if bgm else character.display_name,
                desc=lines,
            )

        return list(await asyncio.gather(*(one(character) for character in characters)))

    async def calendar_data(
        self, date: Sequence[str], stamp: str, *, exclude: Sequence[str] = ()
    ) -> dict[str, Any] | None:
        schedule = self.settings.schedule
        results = await asyncio.gather(
            self.runtime.vndb.request_anniversaries(date, limit=schedule.anniversary_count),
            self.runtime.vndb.request_birthdays(
                date, limit=schedule.birthday_count, exclude=exclude
            ),
            return_exceptions=True,
        )
        blocks, notes = [], []
        for label, result, limit, kind in zip(
            ("作品发布纪念日", "其他角色生日" if exclude else "角色生日"),
            results,
            (schedule.anniversary_count, schedule.birthday_count),
            ("anniversaries", "birthdays"),
            strict=True,
        ):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                logger.warning("[Gal工房] %s清单查询失败：%s", label, brief(result))
                notes.append(f"{label}暂未能查询，其余栏目正常展示。")
                continue
            if not result:
                notes.append(f"{label}：数据源未返回条目。")
                continue
            try:
                cards = (
                    await self.birthday_cards(result)
                    if kind == "birthdays"
                    else await self.daily_work_cards(result, year=int(date[0]))
                )
            except Exception as error:
                logger.warning("[Gal工房] %s清单组卡失败：%s", label, brief(error))
                notes.append(f"{label}资料整理失败，其余栏目正常展示。")
                continue
            block = section(
                text=(
                    f"{label} · 展示 {len(result)} {'位' if kind == 'birthdays' else '部'} · 上限 {limit}",
                ),
                cards=cards,
            )
            block["kind"] = kind
            blocks.append(block)
        if not blocks:
            return None
        notes.append(
            "日期名单来自 VNDB；作品资料优先 Bangumi，无法可靠关联时保留原资料。清单不按精选评分或性别过滤，数量受设置限制，不代表全站完整统计。"
        )
        if exclude:
            notes.append("已单独推送的精选寿星不在此重复列出。")
        return dossier(
            sections=blocks,
            title=f"{stamp} · 今日清单",
            subtitle="作品发布纪念日 / 角色生日",
            notes=notes,
        )

    async def new_release_data(self, date: Sequence[str], stamp: str) -> dict[str, Any] | None:
        works = await self.runtime.vndb.request_new_releases(
            date, limit=self.settings.schedule.new_release_count
        )
        if not works:
            return None
        cards = await self.daily_work_cards(works, year=int(date[0]), new=True)
        block = section(
            text=(
                f"今日首次发售 · 展示 {len(works)} 部 · 上限 {self.settings.schedule.new_release_count}",
            ),
            cards=cards,
        )
        block["kind"] = "releases"
        return dossier(
            sections=[block],
            title=f"{stamp} · 今日新作",
            subtitle="VNDB 首发日期选题 · Bangumi 优先资料",
            notes=(
                "以 VNDB 作品条目的首次发售日期为准，不是今日入库、补丁或移植发行清单；不设评分/票数门槛，尚无评分会明确标注。",
                "日期可能是发行计划或尚未更新，不能据此确认已经上架；请以发行方公告为准。收录可能遗漏，简介最多展示 600 字，评分为查询时可取得的数据。",
            ),
        )
