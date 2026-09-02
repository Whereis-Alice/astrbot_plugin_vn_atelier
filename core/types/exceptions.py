"""插件内部异常。

消息一律采用「用户可见描述：技术细节」的形式，中间用全角冒号分隔。
指令层只把冒号前的部分展示给用户（见 core.utils.text.brief），
完整消息写入日志，这样既不泄露内部细节又便于排查。
"""

from __future__ import annotations

from .enums import CommandType


class Tips(Exception):
    """可以直接告知用户的业务异常基类。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class EarlyReturn(Tips):
    """流程需要提前结束，但已经给用户回过话，无需再次提示。"""


class InvalidArgsException(Tips):
    """指令参数格式不合法。"""

    def __init__(self, command: CommandType, value: str) -> None:
        super().__init__(f"指令参数错误：{command.value}-{value}")


class SessionTimeoutException(Tips):
    """等待用户后续输入超时。"""

    def __init__(self, command: CommandType, value: str) -> None:
        super().__init__(f"等待结果超时，指令失效：{command.value}-{value}")


class ArgsOrNullException(Tips):
    """参数不合法，或按该参数找不到任何结果。"""

    def __init__(self, command: CommandType, value: str) -> None:
        super().__init__(f"参数错误或结果不存在：{command.value}-{value}")


class InternetException(Tips):
    """网络层多次重试后仍然失败。"""

    def __init__(self, url: str) -> None:
        super().__init__(f"请求网络失败：{url}，请检查【安全配置】是否错误")


class AuthorityException(Tips):
    """目标站点返回了鉴权/风控页面而非预期数据。"""

    def __init__(self, message: str) -> None:
        super().__init__(
            f"网络配置错误：网站返回信息为【{message}】，这通常由【安全配置】配置错误导致"
        )


class ResponseException(Tips):
    """响应体为空或结构与预期不符。"""

    def __init__(self, url: str) -> None:
        super().__init__(f"请求结果为空，网站返回内容错误：{url}")


class NoResultException(Tips):
    """检索成功但没有命中任何内容。"""

    def __init__(self, command: CommandType, value: str) -> None:
        super().__init__(f"未搜索到合适内容：{command.value}-{value}")


class NoCacheException(Tips):
    """本地缓存缺失。属于正常控制流，通常被上层静默捕获。"""

    def __init__(self, path: str) -> None:
        super().__init__(f"缓存不存在：{path}")


class SettingException(Tips):
    """插件配置项缺失或填写错误。"""

    def __init__(self, setting: str) -> None:
        super().__init__(f"插件配置项错误：{setting}")


class RenderException(Tips):
    """图片渲染失败。上层会退回纯文本兜底输出。"""

    def __init__(self, detail: str) -> None:
        super().__init__(f"图片渲染失败：{detail}")


class CooldownException(Tips):
    """触发了每用户冷却限制。"""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"操作太频繁，请在 {seconds}s 后再试：cooldown-{seconds}")
