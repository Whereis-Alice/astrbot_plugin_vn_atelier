"""每日推送内容构建。

与其它指令不同，这里没有消息事件可用（由定时任务触发），因此只继承
Composer，产出一串图片 URL 交给 main.py 分发。

精选作品、精选生日、今日清单、今日新作分别组图；任一栏目失败不取消其余栏目。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from time import monotonic

from astrbot.api import logger

from ..types import Base64, CacheGroup, CommandType, Layout
from ..utils import (
    brief,
    paragraphs,
    parse_touchgal_details_async,
    plain_markup,
    vn_lines,
)
from ..utils.bangumi import match_lines, rating_data
from .base import dossier, feature, section
from .calendar import CalendarComposer

_LOG = "[Gal工房]"

_NO_INTRO = "TouchGal暂无该作品简介。"


class DailyDigest(CalendarComposer):
    """构建当天的推送图片。"""

    async def build(self) -> list[str]:
        """返回图片 URL 列表；全部失败时返回空列表。"""
        now = datetime.now()
        date = [str(now.year), f"{now.month:02d}", f"{now.day:02d}"]
        stamp = f"{now.year}年{now.month}月{now.day}日"

        urls: list[str] = []
        self._birthday_ids: set[str] = set()
        schedule = self.settings.schedule
        logger.info(
            "%s 每日推送开始：查询日期=%s，服务器时间=%s；%s",
            _LOG,
            "-".join(date),
            now.astimezone().isoformat(timespec="seconds"),
            schedule.diagnostic_summary,
        )
        builders = []
        if schedule.featured:
            builders.extend(
                (("精选纪念日作品", self._build_event_vn), ("精选生日角色", self._build_event_cha))
            )
        if schedule.calendar:
            builders.append(("今日清单", self._build_calendar))
        if schedule.new_releases:
            builders.append(("今日新作", self._build_new_releases))
        for name, builder in builders:
            started = monotonic()
            logger.info("%s 开始构建每日推送：%s（%s）", _LOG, name, "-".join(date))
            try:
                url = await builder(date, stamp)
            except Exception as error:
                logger.warning(
                    "%s 构建每日推送（%s，%s）失败 [%s]：%s（耗时 %.1f 秒）",
                    _LOG,
                    name,
                    "-".join(date),
                    type(error).__name__,
                    brief(error),
                    monotonic() - started,
                )
                continue
            if url:
                urls.append(url)
                logger.info(
                    "%s 每日推送第 %s 张图片已生成：%s（耗时 %.1f 秒，尚未发送）",
                    _LOG,
                    len(urls),
                    name,
                    monotonic() - started,
                )
            else:
                logger.info("%s 每日推送（%s，%s）未生成图片，跳过发送", _LOG, name, "-".join(date))
        logger.info("%s 每日推送构建完成：%s，共 %s 张独立图片", _LOG, "-".join(date), len(urls))
        return urls

    # -- 简介 -------------------------------------------------------------- #

    async def _intro(self, vn_id: str) -> tuple[list[str], list[Base64]]:
        """去 TouchGal 找中文简介与预览图，失败时给一句占位说明。"""
        try:
            results, _ = await self.runtime.touchgal.request_vn_by_search(
                CommandType.EVENT_TIMED, vn_id
            )
            html = await self.runtime.touchgal.request_html(results[0].uniqueId)
            details = await parse_touchgal_details_async(html)
        except Exception as error:
            logger.debug("%s 获取 %s 的简介失败：%s", _LOG, vn_id, brief(error))
            return [_NO_INTRO], []

        desc = paragraphs(details.description) or [_NO_INTRO]
        previews = await self.covers(details.previews, CacheGroup.TOUCHGAL)
        return desc, previews

    # -- 两种推送形态 ------------------------------------------------------- #

    async def _build_event_vn(self, date: Sequence[str], stamp: str) -> str:
        vn, characters = await self.runtime.vndb.request_by_event_vn(date)
        match = await self.runtime.bangumi.match_vn(vn)
        subject = match.subject
        if subject and subject.summary:
            desc, previews = paragraphs(plain_markup(subject.summary))[:12], []
        else:
            desc, previews = await self._intro(vn.id)
            if desc == [_NO_INTRO]:
                desc = paragraphs(plain_markup(vn.description))[:12] or ["暂无简介。"]
        cover = (
            await self.cover(subject.cover, CacheGroup.BANGUMI)
            if subject and subject.cover
            else (await self.vndb_covers([vn]))[0]
        )
        data = feature(
            main_image=cover,
            subtitle=subject.display_title if subject else vn.display_title,
            info=[*match_lines(match), *vn_lines(vn)],
            ratings=rating_data(subject, vn),
            title=f"{stamp} · 今日发售纪念",
            desc=desc,
            previews=previews,
            cards_title="登场角色",
            cards=await self.character_cards(characters, ignore_vns=True),
        )
        return await self.render(Layout.FEATURE, data)

    async def _build_event_cha(self, date: Sequence[str], stamp: str) -> str:
        characters, vns = await self.runtime.vndb.request_by_event_cha(date)
        cards = await self.birthday_cards(characters, works=vns)
        if len(cards) == 1:
            data = feature(
                main_image=cards[0]["image"],
                subtitle=cards[0]["subtitle"],
                info=cards[0].get("desc", []),
                title=f"{stamp} · 今日生日精选",
                cards_title="登场作品",
                cards=await self.vn_cards(vns),
            )
            layout = Layout.FEATURE
        else:
            birthdays = section(text=(f"同作同日生日 · 共 {len(cards)} 位，一起祝福",), cards=cards)
            birthdays["kind"] = "birthdays"
            data = dossier(
                sections=[birthdays, section(text=("登场作品",), cards=await self.vn_cards(vns))],
                title=f"{stamp} · 今日生日精选",
                subtitle="同一精选作品的寿星一起展示",
            )
            layout = Layout.DOSSIER
        url = await self.render(layout, data)
        # 只有成功生成精选卡才排除；精选失败时，清单仍可展示这些寿星。
        self._birthday_ids = {item.id for item in characters}
        return url

    async def _build_calendar(self, date: Sequence[str], stamp: str) -> str:
        data = await self.calendar_data(date, stamp, exclude=sorted(self._birthday_ids))
        return await self.render(Layout.DOSSIER, data) if data else ""

    async def _build_new_releases(self, date: Sequence[str], stamp: str) -> str:
        data = await self.new_release_data(date, stamp)
        return await self.render(Layout.DOSSIER, data) if data else ""
