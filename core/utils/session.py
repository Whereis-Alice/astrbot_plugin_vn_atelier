"""会话过滤器。"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import SessionFilter


class SenderSessionFilter(SessionFilter):
    """把会话锁定到「同一平台 + 同一会话 + 同一发送者」。

    上游用 group_id 与 sender_id 直接字符串相加做键，"1" + "23" 与
    "12" + "3" 会撞成同一个会话。这里改用 unified_msg_origin 加显式
    分隔符，既覆盖私聊场景也不会碰撞。
    """

    def filter(self, event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}::{event.get_sender_id()}"
