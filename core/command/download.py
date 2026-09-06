"""资源下载：工房 下载 <TouchGal数字ID / VNDB ID / 关键词>。

三种入参走三条路：
* 纯数字 —— 直接当 TouchGal 站内 ID 用。
* v 开头的 VNDB ID 且站内只命中一条 —— 自动选中，不打扰用户。
* 其余 —— 列出候选，等用户回一个编号。

候选列表在支持合并转发的平台上折叠成一条转发消息，其它平台平铺发送。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence

import astrbot.api.message_components as comp
from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.util import SessionController, session_waiter

from ..types import (
    CacheGroup,
    CommandType,
    ResourceResponse,
    SessionTimeoutException,
    TouchGalResponse,
)
from ..utils import SenderSessionFilter, download_lines, search_lines
from .base import BaseCommand

_VNDB_ID = re.compile(r"v\d+")

_NICKNAME = "Gal工房"


class DownloadCommand(BaseCommand):
    command = CommandType.DOWNLOAD

    # -- 候选展示 ----------------------------------------------------------- #

    def _entry(self, index: int, image: str, text: str) -> list[comp.BaseMessageComponent]:
        return [
            comp.Plain(f"【{index}】\n"),
            comp.Image.fromBase64(image),
            comp.Plain(f"\n{text}"),
        ]

    async def _candidates(self, results: Sequence[TouchGalResponse]) -> list[MessageEventResult]:
        """把候选列表变成一条转发消息，或若干条普通消息。"""
        images = await self.covers(
            [item.banner or "" for item in results], CacheGroup.TOUCHGAL, prefix=False
        )
        entries = [
            (index, image, "\n".join(search_lines(item)))
            for index, (item, image) in enumerate(zip(results, images, strict=True), start=1)
        ]

        if self.supports_forward:
            nodes = [
                comp.Node(
                    uin=self.event.get_self_id(),
                    name=_NICKNAME,
                    content=self._entry(index, image, text),
                )
                for index, image, text in entries
            ]
            return [self.event.chain_result([comp.Nodes(nodes)])]

        return [
            self.event.chain_result(self._entry(index, image, text))
            for index, image, text in entries
        ]

    # -- 选号 --------------------------------------------------------------- #

    async def _wait_index(self, count: int) -> int:
        """等用户回一个 1..count 的编号，返回 0 基下标。

        上游用「结果数是否等于站内单页上限」来判断合法区间，一旦搜索结果
        少于一页就会把有效编号判成越界。这里直接以实际条目数为界。
        """
        timeout = self.settings.basic.session_timeout
        chosen: list[int] = []

        @session_waiter(timeout=timeout)
        async def waiter(controller: SessionController, event) -> None:
            message = (event.message_str or "").strip()
            if not message.isdigit() or not 1 <= int(message) <= count:
                await event.send(MessageChain().message(f"请回复 1 到 {count} 之间的编号。"))
                controller.keep(timeout, True)
                return
            chosen.append(int(message) - 1)
            controller.stop()

        try:
            await waiter(self.event, session_filter=SenderSessionFilter())
        except TimeoutError as error:
            raise SessionTimeoutException(self.command.value, "编号") from error

        if not chosen:
            raise SessionTimeoutException(self.command.value, "编号")
        return chosen[0]

    # -- 结果分批 ----------------------------------------------------------- #

    def _batches(self, resources: Sequence[ResourceResponse]) -> list[MessageEventResult]:
        """按 forwardLimit 分批，避免一条转发塞进上百个节点。"""
        blocks = ["\n".join(download_lines(item)) for item in resources]
        limit = max(1, self.settings.basic.forward_limit)
        results: list[MessageEventResult] = []
        for start in range(0, len(blocks), limit):
            chunk = blocks[start : start + limit]
            if self.supports_forward:
                nodes = [
                    comp.Node(
                        uin=self.event.get_self_id(),
                        name=_NICKNAME,
                        content=[comp.Plain(text)],
                    )
                    for text in chunk
                ]
                results.append(self.event.chain_result([comp.Nodes(nodes)]))
            else:
                results.extend(self.text(text) for text in chunk)
        return results

    # -- 主流程 ------------------------------------------------------------- #

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        raw = self.require(value)

        if raw.isdigit():
            patch_id: int | str = raw
        else:
            results, total = await self.runtime.touchgal.request_vn_by_search(self.command, raw)
            if _VNDB_ID.fullmatch(raw.lower()) and total == 1:
                patch_id = results[0].id
            else:
                timeout = self.settings.basic.session_timeout
                yield self.text(
                    "-未识别到ID，改为关键词搜索\n"
                    "-从以下内容中选择一项\n"
                    f"-请在{timeout}s内回复输入对应编号"
                )
                for message in await self._candidates(results):
                    yield message
                patch_id = results[await self._wait_index(len(results))].id

        resources = await self.runtime.touchgal.request_download(patch_id)
        for result in self._batches(resources):
            yield result
