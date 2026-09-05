"""插件内部异常。

消息一律采用「用户可见描述：技术细节」的形式，中间用全角冒号分隔。
指令层只把冒号前的部分展示给用户（见 core.utils.text.brief），
完整消息写入日志，这样既不泄露内部细节又便于排查。
"""

from __future__ import annotations

from .enums import CommandType


def _label(command: CommandType | str) -> str:
    """取指令名。

    异常都在错误路径上构造，本身绝不能再抛异常，所以这里同时容忍枚举与字符串
    ——调用点少写一个 .value 不该把「没搜到结果」变成一条 AttributeError。
    """
    return command.value if isinstance(command, CommandType) else str(command)


class Tips(Exception):
    """可以直接告知用户的业务异常基类。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class EarlyReturn(Tips):
    """流程需要提前结束，但已经给用户回过话，无需再次提示。"""


class InvalidArgsException(Tips):
    """指令参数格式不合法。"""

    def __init__(self, command: CommandType | str, value: str) -> None:
        super().__init__(f"指令参数错误：{_label(command)}-{value}")


class SessionTimeoutException(Tips):
    """等待用户后续输入超时。"""

    def __init__(self, command: CommandType | str, value: str) -> None:
        super().__init__(f"等待结果超时，指令失效：{_label(command)}-{value}")


class ArgsOrNullException(Tips):
    """参数不合法，或按该参数找不到任何结果。"""

    def __init__(self, command: CommandType | str, value: str) -> None:
        super().__init__(f"参数错误或结果不存在：{_label(command)}-{value}")


class InternetException(Tips):
    """网络层多次重试后仍然失败。"""

    def __init__(self, url: str) -> None:
        super().__init__(f"请求网络失败：{url}，请检查【安全配置】是否错误")


class ChallengeException(Tips):
    """目标站点开启了浏览器验证（Cloudflare 挑战等），请求无法直接通过。

    与 InternetException 的区别很重要：网络抖动重试有用，浏览器验证重试没用。
    挑战要求客户端执行一段 JavaScript 再把结果交回去，伪装 TLS 指纹或换个
    User-Agent 都答不上来，只能换出口 IP、带上有效的 cf_clearance，或者让
    真实浏览器（FlareSolverr）替你答一次。所以命中挑战时插件会直接停手并
    熔断该站点一段时间，而不是白跑满重试次数、刷满日志。
    """

    def __init__(self, host: str, detail: str = "") -> None:
        super().__init__(
            f"{host} 开启了浏览器验证，暂时取不到数据，可稍后再试：challenge-{host}-{detail}"
        )


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

    def __init__(self, command: CommandType | str, value: str) -> None:
        super().__init__(f"未搜索到合适内容：{_label(command)}-{value}")


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
