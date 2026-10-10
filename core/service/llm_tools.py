"""LLM 函数工具。

把工房的四个核心检索能力暴露给大模型，用户不必记住指令格式，直接说
「帮我查一下 Clannad 的评分」也能拿到 Bangumi 优先的数据。返回值刻意是纯文本
而不是图片：函数调用的结果要进模型上下文，文本才有意义。

默认关闭（配置项 limit.enableLlmTools），因为工具会占用模型的上下文
预算，并不是所有部署都需要。
"""

from __future__ import annotations

from typing import Any, ClassVar

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from pydantic.dataclasses import dataclass

from ..service.bangumi import subject_id
from ..types import CommandType, Tips
from ..utils import character_lines, clip, producer_lines, vn_lines
from ..utils.bangumi import bangumi_lines, match_lines

_LOG = "[Gal工房]"

_MAX_ITEMS = 3
"""单次工具调用最多返回的条目数，避免刷爆模型上下文。"""

_MAX_CHARS = 1200
"""返回文本的字符上限。"""

_KEYWORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "description": "作品名、角色名或厂商名，支持日文原名、罗马音与常见中译名。",
        }
    },
    "required": ["keyword"],
}


def _block(headline: str, lines: list[str]) -> str:
    return "\n".join([headline, *[f"  {line}" for line in lines]])


def _wrap(command: CommandType, blocks: list[str]) -> str:
    if not blocks:
        return "没有找到匹配的条目。"
    text = "\n\n".join(blocks)
    logger.debug("%s LLM 工具 %s 返回 %s 个条目", _LOG, command.value, len(blocks))
    return clip(text, _MAX_CHARS)


@dataclass
class _AtelierTool(FunctionTool):
    """工房工具基类。

    runtime 声明成 ClassVar，这样它不会变成 pydantic 字段（否则每次注册
    都要对整个依赖容器做一遍校验）；实例化后再逐个赋值即可。
    """

    runtime: ClassVar[Any] = None

    def _keyword(self, kwargs: dict[str, Any]) -> str:
        return str(kwargs.get("keyword") or "").strip()


@dataclass
class SearchVnTool(_AtelierTool):
    """按关键词查作品。"""

    async def call(self, context: ContextWrapper, **kwargs: Any) -> ToolExecResult:
        keyword = self._keyword(kwargs)
        if not keyword:
            return "缺少参数 keyword。"
        try:
            if self.runtime.settings.bangumi.enabled:
                try:
                    subjects = await self.runtime.bangumi.search(keyword, limit=_MAX_ITEMS)
                except Tips:
                    subjects = []
                if subjects:
                    return _wrap(
                        CommandType.BANGUMI,
                        [
                            _block(f"《{item.display_title}》", bangumi_lines(item, compact=True))
                            for item in subjects
                        ],
                    )
            results = await self.runtime.vndb.request_by_vn(keyword)
        except Tips as error:
            return f"查询失败：{error.message}"
        blocks = []
        for item in results[:_MAX_ITEMS]:
            match = await self.runtime.bangumi.match_vn(item)
            blocks.append(
                _block(f"《{item.display_title}》", [*match_lines(match), *vn_lines(item)])
            )
        return _wrap(CommandType.VN, blocks)


@dataclass
class SearchBangumiTool(_AtelierTool):
    async def call(self, context: ContextWrapper, **kwargs: Any) -> ToolExecResult:
        keyword = self._keyword(kwargs)
        if not keyword:
            return "缺少参数 keyword。"
        try:
            identifier = subject_id(keyword)
            if identifier:
                item = await self.runtime.bangumi.subject(identifier)
                items = [item] if item else []
            else:
                items = await self.runtime.bangumi.search(keyword, limit=_MAX_ITEMS)
            return _wrap(
                CommandType.BANGUMI,
                [
                    _block(f"《{item.display_title}》", bangumi_lines(item))
                    for item in items[:_MAX_ITEMS]
                ],
            )
        except Tips as error:
            return f"查询失败：{error.message}"


@dataclass
class SearchCharacterTool(_AtelierTool):
    """按关键词查角色。"""

    async def call(self, context: ContextWrapper, **kwargs: Any) -> ToolExecResult:
        keyword = self._keyword(kwargs)
        if not keyword:
            return "缺少参数 keyword。"
        try:
            results = await self.runtime.vndb.request_by_character(keyword)
        except Tips as error:
            return f"查询失败：{error.message}"
        options = self.runtime.settings.character.options
        blocks = [
            _block(item.display_name, character_lines(item, options=options))
            for item in results[:_MAX_ITEMS]
        ]
        return _wrap(CommandType.CHARACTER, blocks)


@dataclass
class SearchProducerTool(_AtelierTool):
    """按关键词查厂商及其代表作。"""

    async def call(self, context: ContextWrapper, **kwargs: Any) -> ToolExecResult:
        keyword = self._keyword(kwargs)
        if not keyword:
            return "缺少参数 keyword。"
        try:
            producers, groups = await self.runtime.vndb.request_by_producer(keyword)
        except Tips as error:
            return f"查询失败：{error.message}"
        blocks: list[str] = []
        for producer, works in list(zip(producers, groups, strict=True))[:_MAX_ITEMS]:
            lines = producer_lines(producer)
            titles = [f"《{vn.display_title}》" for vn in works[:6]]
            if titles:
                lines.append(f"代表作：{'、'.join(titles)}")
            blocks.append(_block(producer.display_name, lines))
        return _wrap(CommandType.PRODUCER, blocks)


TOOL_NAMES: tuple[str, ...] = (
    "vn_atelier_search_vn",
    "vn_atelier_search_character",
    "vn_atelier_search_producer",
    "vn_atelier_search_bangumi",
)


def build_tools(runtime: Any) -> list[FunctionTool]:
    """构造四个工具实例并注入运行时依赖。"""
    specs = (
        (
            SearchVnTool,
            TOOL_NAMES[0],
            (
                "优先在 Bangumi 检索游戏，失败或无结果时回退 VNDB，返回注明来源与量纲的评分、发售日、"
                "制作方、游玩时长与多语言标题。需要作品客观资料时使用。"
            ),
        ),
        (
            SearchCharacterTool,
            TOOL_NAMES[1],
            (
                "在 VNDB 上按名称检索视觉小说角色，返回别称、生日、出场作品与身体数据。"
                "需要角色资料时使用。"
            ),
        ),
        (
            SearchProducerTool,
            TOOL_NAMES[2],
            "在 VNDB 上按名称检索视觉小说厂商 / 社团，返回类型、文本语言与代表作列表。",
        ),
        (
            SearchBangumiTool,
            TOOL_NAMES[3],
            "读取 Bangumi 游戏的评分（10分制）、评价人数、游戏排名、发售日和制作数据。keyword 可为名称、条目ID或 bgm.tv 条目链接。不能用动漫条目的评分代替游戏评分。",
        ),
    )
    tools: list[FunctionTool] = []
    for factory, name, description in specs:
        tool = factory(name=name, description=description, parameters=_KEYWORD_SCHEMA)
        tool.runtime = runtime
        tools.append(tool)
    return tools
