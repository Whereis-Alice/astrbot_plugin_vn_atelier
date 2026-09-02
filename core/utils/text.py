"""文本处理小工具。"""

from __future__ import annotations

import re
from collections.abc import Iterable

_FULLWIDTH_COLON = "："
_BLANK_LINES = re.compile(r"\n{2,}")


def brief(error: BaseException | str) -> str:
    """取异常消息中「用户可见描述」的部分。

    插件内部异常统一写成「描述：细节」，这里只保留全角冒号之前的描述，
    避免把 URL、payload 之类的技术细节直接抛给群里。
    """
    message = str(error).strip()
    if not message:
        return "未知错误"
    return message.split(_FULLWIDTH_COLON, 1)[0]


def paragraphs(text: str | None, limit: int = 0) -> list[str]:
    """把多行简介切成段落数组。

    渲染层负责转义与换行，因此这里只输出纯文本段落，不生成任何 HTML。

    Args:
        text: 原始简介，可能含 CRLF 与连续空行。
        limit: 最多保留的段落数，0 表示不限制。
    """
    if not text:
        return []
    normalized = _BLANK_LINES.sub("\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    result = [line.strip() for line in normalized.split("\n") if line.strip()]
    return result[:limit] if limit > 0 else result


def join_cn(items: Iterable[object] | None, separator: str = "、") -> str:
    """用中文顿号连接可迭代对象，跳过空值。"""
    if not items:
        return ""
    return separator.join(str(i).strip() for i in items if str(i).strip())


def clip(text: str, limit: int, ellipsis: str = "…") -> str:
    """按字符数截断文本。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - len(ellipsis), 1)] + ellipsis
