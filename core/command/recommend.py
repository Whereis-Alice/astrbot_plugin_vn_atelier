"""标签推荐：工房 推荐 <标签...>。

交互形态是「一张图 + 一句提示 → 用户回【换一个】继续 / 回【结束】收尾」。
为了让「换一个」几乎不用等，这里跑一个生产者协程在后台预渲染若干张图，
消费侧只从就绪队列里取。

与上游的差异：
* 生产协程在指令结束时一定会被 cancel，不再有悬挂任务。
* 生产侧异常不会淹没在后台，而是通过 stop_info 反馈给用户。
* 就绪队列满时生产侧会真正挂起等待，而不是空转轮询。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from astrbot.api import logger
from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.util import SessionController, session_waiter

from ..types import (
    CacheGroup,
    CommandType,
    Layout,
    RecommendSession,
    Tips,
    TouchGalResponse,
)
from ..utils import (
    SenderSessionFilter,
    brief,
    paragraphs,
    parse_touchgal_details_async,
    plain_markup,
    search_lines,
)
from ..utils.bangumi import match_lines, rating_data
from .base import BaseCommand, feature

_LOG = "[Gal工房]"

_NEXT_WORDS = frozenset({"换一个", "换一张", "下一个", "next"})
_STOP_WORDS = frozenset({"结束", "停止", "退出", "stop"})


class RecommendCommand(BaseCommand):
    command = CommandType.RECOMMEND

    # -- 渲染单个候选 ------------------------------------------------------- #

    async def _render_one(self, item: TouchGalResponse) -> str:
        html = await self.runtime.touchgal.request_html(item.uniqueId)
        details = await parse_touchgal_details_async(html)
        match = await self.touchgal_match(details)
        subject = match.subject
        data = feature(
            main_image=await self.cover(subject.cover, CacheGroup.BANGUMI)
            if subject and subject.cover
            else await self.cover(item.banner, CacheGroup.TOUCHGAL),
            subtitle=subject.display_title if subject else item.name or details.title,
            info=[*match_lines(match), *search_lines(item, ignore_name=True)],
            ratings=rating_data(subject),
            title="标签推荐",
            desc=paragraphs(plain_markup(subject.summary))[:12]
            if subject and subject.summary
            else paragraphs(details.description),
            previews=await self.covers(details.previews, CacheGroup.TOUCHGAL),
        )
        return await self.render(Layout.FEATURE, data)

    # -- 生产者 ------------------------------------------------------------- #

    async def _produce(self, session: RecommendSession, per_page: int) -> None:
        """后台预渲染，直到候选耗尽或被取消。"""
        cache_size = self.settings.recommend.cache_size
        try:
            while True:
                if session.handled >= session.total and not session.pending:
                    break
                if len(session.ready) > cache_size:
                    session.use_signal.clear()
                    await session.use_signal.wait()
                    continue
                if not session.pending:
                    page = session.handled // per_page + 1
                    results, _ = await self.runtime.touchgal.request_vn_by_search(
                        self.command,
                        session.keyword,
                        search_in_alias=False,
                        search_in_tag=True,
                        limit=per_page,
                        page=page,
                    )
                    if not results:
                        break
                    session.pending.extend(results)

                item = session.pending.pop(0)
                session.handled += 1
                try:
                    url = await self._render_one(item)
                except Tips as error:
                    logger.debug("%s 跳过候选 %s：%s", _LOG, item.uniqueId, error.message)
                    continue
                session.ready.append(url)
                session.ready_signal.set()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            session.stop_info = brief(error)
            logger.warning("%s 推荐预取中断：%s（%s）", _LOG, session.stop_info, session.snapshot())
        finally:
            session.stop_signal.set()

    # -- 消费者 ------------------------------------------------------------- #

    @staticmethod
    async def _next(session: RecommendSession) -> str:
        """取下一张就绪图片；没有更多结果时抛 Tips。"""
        while True:
            if session.ready:
                url = session.ready.pop(0)
                if not session.ready:
                    session.ready_signal.clear()
                session.use_signal.set()
                return url
            if session.stop_signal.is_set():
                raise Tips(session.stop_info or "没有更多符合条件的作品了。")

            waiters = [
                asyncio.create_task(session.ready_signal.wait()),
                asyncio.create_task(session.stop_signal.wait()),
            ]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waiters:
                    task.cancel()

    # -- 主流程 ------------------------------------------------------------- #

    def _tips(self) -> str:
        timeout = self.settings.basic.session_timeout
        return (
            "-如果需要以同样要求继续获取作品，请输入文本【换一个】\n"
            "-如果不再需要，请输入文本【结束】以结束此次会话\n"
            "-后续同理\n"
            f"-默认等待时间：{timeout}s"
        )

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        keyword = self.require(value)
        per_page = max(1, self.settings.recommend.cache_size * 3)

        results, total = await self.runtime.touchgal.request_vn_by_search(
            self.command,
            keyword,
            search_in_alias=False,
            search_in_tag=True,
            limit=per_page,
            page=1,
        )
        session = RecommendSession(pending=list(results), total=total, keyword=keyword)
        task = asyncio.create_task(self._produce(session, per_page))
        try:
            yield self.event.image_result(await self._next(session))
            yield self.text(self._tips())
            await self._interact(session)
        finally:
            task.cancel()

    async def _interact(self, session: RecommendSession) -> None:
        """会话循环。超时静默结束，用户说【结束】则收尾。"""
        timeout = self.settings.basic.session_timeout

        @session_waiter(timeout=timeout)
        async def waiter(controller: SessionController, event) -> None:
            controller.keep(timeout, True)
            message = (event.message_str or "").strip()
            if message in _STOP_WORDS:
                await event.send(MessageChain().message("已结束本次推荐，感谢使用。"))
                controller.stop()
                return
            if message not in _NEXT_WORDS:
                return
            try:
                url = await self._next(session)
            except Tips as error:
                await event.send(MessageChain().message(error.message))
                controller.stop()
                return
            await event.send(MessageChain().url_image(url))

        try:
            await waiter(self.event, session_filter=SenderSessionFilter())
        except TimeoutError:
            return
