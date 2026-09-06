"""出处识别：工房 出处 [图片URL]，或直接带图 / 引用带图的消息。

图片来源按四级优先取用：指令参数里的 URL → 本条消息里的图片 → 被引用
消息里的图片 → 等用户下一条消息补发。上游只支持前两级。

识别结果按检测框分节展示，每节左侧是裁下来的区域图，右侧列出候选角色
在 VNDB 上的档案；VNDB 查不到的候选也会保留，只是标注「可能并非来自
Gal」，而不是直接丢掉。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

import astrbot.api.message_components as comp
from astrbot.api import logger
from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.util import SessionController, session_waiter

from ..types import (
    AnimeTraceData,
    Base64,
    CommandType,
    DetectedInfo,
    NoResultException,
    SessionTimeoutException,
    VndbCharacterResponse,
)
from ..utils import (
    FileIO,
    SenderSessionFilter,
    character_lines,
    crop_box_async,
    to_jpeg_async,
)
from .base import BaseCommand, card, dossier, section

_LOG = "[Gal工房]"
_URL = re.compile(r"https?://\S+")


class FindCommand(BaseCommand):
    command = CommandType.FIND

    # -- 取图 --------------------------------------------------------------- #

    @staticmethod
    def _first_image(chain: Sequence[comp.BaseMessageComponent] | None) -> comp.Image | None:
        for part in chain or []:
            if isinstance(part, comp.Image):
                return part
        return None

    async def _from_message(self) -> str:
        """从本条消息或被引用的消息里取图，返回 base64。"""
        chain = self.event.get_messages()

        image = self._first_image(chain)
        if image is not None:
            return await image.convert_to_base64()

        for part in chain or []:
            if isinstance(part, comp.Reply):
                quoted = self._first_image(part.chain)
                if quoted is not None:
                    return await quoted.convert_to_base64()
        return ""

    async def _wait_image(self) -> str:
        """等用户补发一张图片。"""
        timeout = self.settings.basic.session_timeout
        holder: list[str] = []

        @session_waiter(timeout=timeout)
        async def waiter(controller: SessionController, event) -> None:
            image = self._first_image(event.get_messages())
            if image is None:
                await event.send(MessageChain().message("未解析到图片，请重新发送"))
                controller.keep(timeout, True)
                return
            holder.append(await image.convert_to_base64())
            controller.stop()

        try:
            await waiter(self.event, session_filter=SenderSessionFilter())
        except TimeoutError as error:
            raise SessionTimeoutException(self.command.value, "图片") from error

        if not holder:
            raise SessionTimeoutException(self.command.value, "图片")
        return holder[0]

    # -- 候选角色 ----------------------------------------------------------- #

    async def _candidate_cards(self, candidates: Sequence[DetectedInfo]) -> list[dict]:
        """把候选角色逐个换成 VNDB 档案卡。"""
        picked = list(candidates)[: max(1, self.settings.find.results)]
        found = await asyncio.gather(
            *(self.runtime.vndb.request_by_find(item.character, item.work) for item in picked),
            return_exceptions=True,
        )

        images = await self.vndb_covers(
            [item for item in found if isinstance(item, VndbCharacterResponse)]
        )
        fallback = await self.error_image()

        cards: list[dict] = []
        cursor = 0
        for candidate, result in zip(picked, found, strict=True):
            if isinstance(result, VndbCharacterResponse):
                cards.append(
                    card(
                        image=images[cursor],
                        subtitle=result.display_name,
                        desc=character_lines(result, ignore_extra=True),
                    )
                )
                cursor += 1
            else:
                cards.append(
                    card(
                        image=fallback,
                        subtitle=candidate.character,
                        desc=[
                            f"出场作品：{candidate.work}",
                            "VNDB暂无记录，角色可能并非来自Gal",
                        ],
                    )
                )
        return cards

    # -- 主流程 ------------------------------------------------------------- #

    async def _sections(self, boxes: Sequence[AnimeTraceData], raw: bytes) -> list[dict]:
        blocks: list[dict] = []
        for item in boxes:
            crop = b""
            if raw:
                try:
                    crop = await crop_box_async(raw, item.box)
                except Exception as error:
                    logger.debug("%s 裁剪检测区域失败：%s", _LOG, error)
            confidence = "不可信" if item.not_confident else "可信"
            blocks.append(
                section(
                    text=("检测区域如左图", f"可信度：{confidence}"),
                    image=FileIO.to_base64(crop) if crop else await self.error_image(),
                    cards=await self._candidate_cards(item.character),
                )
            )
        return blocks

    async def execute(self, value: str) -> AsyncIterator[MessageEventResult]:
        matched = _URL.search(value or "")
        image: str = matched.group(0) if matched else ""
        if not image:
            image = await self._from_message()
        if not image:
            timeout = self.settings.basic.session_timeout
            yield self.text(
                f"-未检测到有效的指令参数\n-改为从下一条消息中获取\n-请在{timeout}s内发送一张图片"
            )
            image = await self._wait_image()

        response, model = await self.runtime.animetrace.request_find(image)
        if not response.data:
            raise NoResultException(self.command, "图片")

        raw = await self._raw_bytes(image)
        hero = {
            "image": FileIO.to_base64(raw) if raw else await self.error_image(),
            "text": [
                f"识别模型「{model}」",
                f"匹配数「{len(response.data)}」个",
                f"AI图「{'是' if response.ai else '否'}」",
            ],
        }
        data = dossier(
            sections=await self._sections(response.data, raw),
            title="出处识别",
            subtitle="数据来自 AnimeTrace + VNDB",
            hero=hero,
            notes=("识别结果仅供参考，可信度低时建议换一张更清晰的角色特写。",),
        )
        yield await self.picture(data)

    async def _raw_bytes(self, image: Base64) -> bytes:
        """把入参统一成 JPEG 字节，供裁剪与主视觉使用。"""
        if image.lower().startswith(("http://", "https://")):
            downloaded = await self.runtime.downloader.download(image)
            return await to_jpeg_async(downloaded) if downloaded else b""
        return FileIO.from_base64(image)
