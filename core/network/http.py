"""统一的 HTTP 客户端。

除了常规的重试与超时，这一层还负责识别「浏览器验证」（Cloudflare 挑战）并
把它与普通网络故障区分开。两者的处理方式正好相反：

* 网络抖动 —— 重试有用，退避后再来一次。
* 浏览器验证 —— 重试没用。挑战要求客户端执行一段 JavaScript 再交回答案，
  换 User-Agent、伪装 TLS 指纹都答不上来。继续重试只会让指令响应变慢、
  日志里刷满同一条警告。

所以命中验证时会立刻停手，把该站点熔断一小段时间，并且只写一条带解决办法
的警告。在熔断之前还会依次试两条出路：先用用户手填的 cf_clearance 重放一次，
再尝试已配置的 FlareSolverr；两种方式都不保证验证成功。

cf_clearance 的有效性受有效期、客户端环境和站点策略影响。迁移服务器后，
旧值不能视为仍然有效；未填写 UA 也不等于已经证明 UA 不匹配。
日志只报告实际响应与配置状态，不把可能原因写成确定结论。
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import aiohttp
from astrbot.api import logger

from ..settings import Settings
from ..types import ChallengeException, InternetException
from ..utils import clip

ResType = Literal["text", "json", "bytes"]

_LOG = "[Gal工房]"

_JSON_HEADERS = {"Content-Type": "application/json"}

_CHALLENGE_STATUS = frozenset({403, 503})
"""可能是验证页的状态码。真正是不是还要看响应头与正文特征。"""

_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "attention required!",
    "enable javascript and cookies",
    "verifying you are human",
)

_MITIGATION_HEADER = "cf-mitigated"
"""Cloudflare 拦截时会带上这个响应头，值为 challenge。比猜正文可靠得多。"""

_PEEK_BYTES: Final = 4096
"""判定验证页时最多读取的正文字节数。挑战页只有几 KB，够用了。"""

_CHALLENGE_COOLDOWN: Final = 300
"""命中验证后熔断多少秒。"""

_CLEARANCE_TTL: Final = 900
"""cf_clearance 的复用时长（秒）。实际寿命由站点配置决定，这里取保守值。"""

_SOLVER_TIMEOUT_MS: Final = 60000
"""留给 FlareSolverr 跑完挑战的时间。真实浏览器启动 + 等待 JS 需要几十秒。"""

_ADVICE = (
    "请先核对「TouchGal 站点地址」：部署过 Worker 不会自动启用，"
    "需要在当前实例中填写完整地址（含访问口令路径）。"
    "迁移服务器后还应检查代理配置，并清理已失效的旧 cf_clearance。"
    "验证可能与出口 IP、客户端环境或站点策略有关，不能仅凭 403 确定原因。"
    "配置说明见 docs/faq.md「站点开启浏览器验证怎么办」（README 有入口）。"
)

_CLEARANCE_EPOCH_LOW: Final = 1_500_000_000
_CLEARANCE_EPOCH_HIGH: Final = 4_000_000_000
"""判定「某一段是不是签发时间戳」的合理区间，约 2017 年到 2096 年。"""

_MISS: Final = object()
"""内部哨兵：表示「没有拿到结果」，与合法的 None/空字符串区分开。"""


def looks_like_challenge(body: str) -> bool:
    """判断响应体是否是 Cloudflare 之类的人机校验页面。"""
    sample = body[:2048].lower()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


def mitigation_of(headers: Mapping[str, str] | Any) -> str:
    """从响应头里取 Cloudflare 的拦截标记，未命中时返回空串。"""
    try:
        value = headers.get(_MITIGATION_HEADER)
    except AttributeError:
        return ""
    return str(value).strip() if value else ""


def clearance_issued_at(token: str) -> float:
    """从 cf_clearance 里读出签发时间（unix 秒），读不出来时返回 0。

    Cloudflare 把签发时刻以明文放在用短横线分隔的其中一段里，形如
    ``<随机串>-<1757074232>-<版本>-<签名>``。这不是公开契约，但只用来生成
    「这张通行证是几点签的」这句提示，判断错也只是提示不准，不影响请求。
    """
    for segment in token.split("-"):
        if not segment.isdigit():
            continue
        moment = int(segment)
        if _CLEARANCE_EPOCH_LOW <= moment <= _CLEARANCE_EPOCH_HIGH:
            return float(moment)
    return 0.0


def describe_age(issued_at: float, now: float | None = None) -> str:
    """把签发时间说成人话，例如「4 小时 48 分钟前签发」。"""
    if issued_at <= 0:
        return "签发时间未知"
    seconds = int((time.time() if now is None else now) - issued_at)
    if seconds < 0:
        return "签发时间在未来，本机时钟可能不准"
    if seconds < 60:
        return f"{seconds} 秒前签发"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分钟前签发"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分钟前签发"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时前签发"


def ua_for_profile(profile: str, fallback: str) -> str:
    """按 TLS 指纹档位给出配套的 User-Agent。

    curl_cffi 伪装的是 TLS/HTTP2 握手指纹，UA 仍然要自己声明。两者对不上
    （指纹说 Chrome 136、UA 说 Chrome 147）本身就是一条风控特征，所以这里从
    ``chrome136`` 这类档位名里取出主版本号拼一个匹配的 UA。认不出来的档位
    （safari、firefox 等）保持调用方给的默认值。
    """
    name = profile.strip().lower()
    if not name.startswith(("chrome", "edge")):
        return fallback
    major = "".join(ch for ch in name if ch.isdigit())[:3]
    if len(major) < 2:
        return fallback
    base = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )
    return f"{base} Edg/{major}.0.0.0" if name.startswith("edge") else base


def host_of(url: str) -> str:
    """取 URL 的主机名，解析失败时退回原始字符串。"""
    return urlsplit(url).netloc or url


def origin_of(url: str) -> str:
    """取 URL 的站点根地址。验证求解针对站点整体，不针对某个接口。"""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return f"{parts.scheme}://{parts.netloc}/"


# --------------------------------------------------------------------------- #
# 验证状态
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Clearance:
    """一张浏览器验证通行证。

    尽量保持取得它时的出口与 User-Agent。改变客户端环境可能使验证失效，
    因此把 UA 一起存下来，重放时原样带上；最终以站点实际响应为准。
    """

    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    expire: float = 0.0

    @property
    def alive(self) -> bool:
        return bool(self.cookies) and time.monotonic() < self.expire

    @property
    def headers(self) -> dict[str, str]:
        return {"user-agent": self.user_agent} if self.user_agent else {}


class ChallengeGuard:
    """按站点记录浏览器验证状态的熔断器。

    命中验证之后的短时间内，对该站点的每一次请求都注定失败。熔断期内直接
    短路，可以让指令立刻给出可读的提示，而不是让用户等满一轮重试；同时把
    N 条重复警告压缩成进入熔断时的一条。
    """

    def __init__(self, cooldown: int = _CHALLENGE_COOLDOWN) -> None:
        self._cooldown = cooldown
        self._until: dict[str, float] = {}

    def blocked(self, host: str) -> int:
        """返回剩余熔断秒数，0 表示可以正常请求。"""
        deadline = self._until.get(host)
        if deadline is None:
            return 0
        remain = deadline - time.monotonic()
        if remain <= 0:
            self._until.pop(host, None)
            return 0
        return int(remain) + 1

    def trip(self, host: str) -> bool:
        """标记命中验证，返回本次是否是新一轮熔断的开始。"""
        first = self.blocked(host) == 0
        self._until[host] = time.monotonic() + self._cooldown
        return first

    def clear(self, host: str) -> None:
        """请求成功，解除该站点的熔断。"""
        self._until.pop(host, None)

    def reset(self) -> None:
        self._until.clear()


class Http:
    """带重试、验证识别、熔断与 curl_cffi 兜底的通用客户端。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._guard = ChallengeGuard()
        self._passes: dict[str, Clearance] = {}
        self._manual_dead: dict[str, str] = {}
        """记下「哪个站点上、哪一串手填 cf_clearance 已经证明无效」。

        存的是 cookie 本身而不只是站点名：用户在 WebUI 里换了新值就应该再试
        一次，没换就不该每次请求都重放一遍注定失败的请求、刷一遍同样的日志。
        """

    # -- 生命周期 ---------------------------------------------------------- #

    async def initialize(self) -> None:
        """在事件循环内创建连接池。

        绝不能在模块导入期创建 ClientSession/TCPConnector，否则插件热重载
        之后它仍然绑定在已经关闭的旧事件循环上，所有请求都会失败。
        """
        await self._ensure_session()

    async def terminate(self) -> None:
        """关闭连接池。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._guard.reset()
        self._passes.clear()
        self._manual_dead.clear()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=self._settings.basic.request_timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    # -- 对外接口 ---------------------------------------------------------- #

    @property
    def proxy(self) -> str | None:
        """aiohttp 风格的代理地址，未配置时为 None。"""
        return self._settings.safety.proxy or None

    def challenged(self, url: str) -> int:
        """该站点当前剩余的熔断秒数，供指令层决定是否直接走备选数据源。"""
        return self._guard.blocked(host_of(url))

    async def get(
        self,
        url: str,
        *,
        res_type: ResType = "text",
        err_handle: Any = None,
        handle_cf: bool = False,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """发起 GET 请求。

        Args:
            url: 目标地址。
            res_type: 期望的响应形态，text / json / bytes。
            err_handle: 全部尝试失败时返回的兜底值；为 None 则抛异常。
            handle_cf: 普通失败后是否用 curl_cffi 模拟浏览器指纹再试一次。
                命中真正的 JS 验证时不会走这条路——指纹伪装对它无效。
            headers: 额外请求头，会覆盖默认的 Content-Type。
            cookies: 额外 Cookie。
            params: URL 查询参数。
        """
        return await self._request(
            "GET",
            url,
            res_type=res_type,
            err_handle=err_handle,
            handle_cf=handle_cf,
            headers=headers,
            cookies=cookies,
            params=params,
        )

    async def post(
        self,
        url: str,
        data: Any = None,
        *,
        res_type: ResType = "json",
        err_handle: Any = None,
        handle_cf: bool = False,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Any:
        """发起 POST 请求，data 会以 JSON 形式提交。"""
        return await self._request(
            "POST",
            url,
            res_type=res_type,
            err_handle=err_handle,
            handle_cf=handle_cf,
            headers=headers,
            cookies=cookies,
            body=data,
        )

    # -- 内部实现 ---------------------------------------------------------- #

    async def _request(
        self,
        method: str,
        url: str,
        *,
        res_type: ResType,
        err_handle: Any = None,
        handle_cf: bool = False,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        if not url.lower().startswith(("http://", "https://")):
            logger.warning("%s 非法的请求地址：%s", _LOG, url)
            if err_handle is not None:
                return err_handle
            raise InternetException(url)

        host = host_of(url)
        waiting = self._guard.blocked(host)
        if waiting:
            # 熔断期内不再空跑：验证不会因为多请求几次就消失。
            if err_handle is not None:
                return err_handle
            raise ChallengeException(host, f"熔断剩余 {waiting}s")

        merged = {**_JSON_HEADERS, **(headers or {})}
        jar = dict(cookies or {})
        self._attach_pass(host, merged, jar)

        retries = self._settings.basic.request_retries
        detail = "未知错误"
        challenge = ""

        for attempt in range(1, retries + 1):
            try:
                session = await self._ensure_session()
                async with session.request(
                    method,
                    url,
                    headers=merged,
                    cookies=jar or None,
                    params=params,
                    json=body,
                    proxy=self.proxy,
                    allow_redirects=True,
                ) as response:
                    if response.status >= 400:
                        detail = f"HTTP {response.status}"
                        challenge = await self._diagnose(response)
                        if challenge:
                            detail = f"HTTP {response.status}（{challenge}）"
                            break
                        if response.status < 500:
                            logger.warning(
                                "%s %s 返回 %s，重试无意义，放弃", _LOG, url, response.status
                            )
                            break
                        logger.warning(
                            "%s %s 返回 %s（第 %s/%s 次尝试）",
                            _LOG,
                            url,
                            response.status,
                            attempt,
                            retries,
                        )
                    elif res_type == "bytes":
                        self._guard.clear(host)
                        return await response.read()
                    else:
                        text = await response.text()
                        if looks_like_challenge(text):
                            challenge = "响应体是人机校验页"
                            detail = challenge
                            break
                        self._guard.clear(host)
                        return jsonlib.loads(text) if res_type == "json" else text
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                detail = f"{type(error).__name__}: {error}"
                logger.warning(
                    "%s 请求 %s 失败（第 %s/%s 次）：%s",
                    _LOG,
                    url,
                    attempt,
                    retries,
                    detail,
                )
            if attempt < retries:
                await asyncio.sleep(0.5 * attempt)

        if challenge:
            solved = await self._bypass_and_replay(
                method, url, host, res_type, merged, jar, params, body
            )
            if solved is not _MISS:
                self._guard.clear(host)
                return solved
            if self._guard.trip(host):
                logger.warning(
                    "%s %s 开启了浏览器验证（%s），接下来 %s 秒内不再请求该站点。%s",
                    _LOG,
                    host,
                    challenge,
                    _CHALLENGE_COOLDOWN,
                    _ADVICE,
                )
            if err_handle is not None:
                return err_handle
            raise ChallengeException(host, detail)

        if handle_cf:
            fallback = await self._curl(method, url, res_type, merged, jar, params, body)
            if fallback is not None:
                self._guard.clear(host)
                return fallback

        logger.warning("%s 放弃请求 %s：%s", _LOG, url, detail)
        if err_handle is not None:
            return err_handle
        raise InternetException(url)

    # -- 验证识别与求解 ---------------------------------------------------- #

    @staticmethod
    async def _diagnose(response: aiohttp.ClientResponse) -> str:
        """判断一个错误响应是否是浏览器验证，命中时返回可读的原因。

        先看 cf-mitigated 响应头，这是 Cloudflare 明确告知「我拦下了」的信号，
        比匹配正文可靠。没有该头时才退回读一小段正文做特征匹配，并且只对
        403/503 这两个状态码做——其余 4xx/5xx 与验证无关。
        """
        mitigation = mitigation_of(response.headers)
        if mitigation:
            return f"{_MITIGATION_HEADER}: {mitigation}"
        if response.status not in _CHALLENGE_STATUS:
            return ""
        try:
            raw = await response.content.read(_PEEK_BYTES)
        except Exception:  # 读正文失败不影响结论，按「不是验证」处理
            return ""
        return "人机校验页" if looks_like_challenge(raw.decode("utf-8", "ignore")) else ""

    @property
    def _manual_pass(self) -> Clearance | None:
        """把用户手填的 cf_clearance 包成一张通行证，没填则返回 None。"""
        safety = self._settings.safety
        if not safety.cf_clearance:
            return None
        return Clearance(
            cookies={"cf_clearance": safety.cf_clearance},
            user_agent=safety.browser_ua,
            expire=time.monotonic() + _CLEARANCE_TTL,
        )

    def _manual_verdict(self, host: str) -> str:
        """报告失败事实与配置线索，不把推测当成验证失败的确定原因。"""
        safety = self._settings.safety
        age = describe_age(clearance_issued_at(safety.cf_clearance))
        ua_note = (
            "已填写「浏览器 User-Agent」，未验证是否与取值浏览器一致"
            if safety.browser_ua
            else "未填写「浏览器 User-Agent」，无法确认是否与取值浏览器一致"
        )
        return (
            f"手填的 cf_clearance 通不过 {host} 的验证（{age}，{ua_note}）。"
            "可能原因包括凭证过期、出口 IP 或客户端环境变化、站点策略变化。"
            "以上时间来自 cookie 格式推测，不能据此确定有效期。"
            "本次验证已失败，将不再反复重试同一值；可清空旧值。"
            "如果迁移前使用 Worker，请核对当前「TouchGal 站点地址」是否仍是直连地址。"
        )

    def _attach_pass(self, host: str, headers: dict[str, str], jar: dict[str, str]) -> None:
        """把该站点缓存中的通行证附加到本次请求上。

        缓存里可能是 FlareSolverr 求解得来的，也可能是手填的 cf_clearance 被
        实测证明有效之后存进来的。两者都只对「确实需要它」的站点生效，不会把
        cookie 撒给 VNDB、渲染服务这些从不做验证的地址。
        """
        current = self._passes.get(host)
        if current is None:
            return
        if not current.alive:
            self._passes.pop(host, None)
            return
        headers.update(current.headers)
        jar.update(current.cookies)

    async def _bypass_and_replay(
        self,
        method: str,
        url: str,
        host: str,
        res_type: ResType,
        headers: dict[str, str],
        jar: dict[str, str],
        params: dict[str, Any] | None,
        body: Any,
    ) -> Any:
        """命中验证后依次试两条出路，都不通才让调用方去熔断。

        ① 用户手填的 cf_clearance —— 不花任何额外资源，值得试，但它只在
           「AstrBot 与取 cookie 的浏览器同一个出口 IP」时才可能有效，所以只在
           这里试一次；成功就缓存起来给该站点后续请求复用，失败就记下这一串已经
           无效，直到用户换成新值为止不再重复。
        ② FlareSolverr —— 真实浏览器现场求解，未配置时不产生任何额外请求。
        """
        # 走到这一步说明手上那张通行证（如果有）已经失效了，先丢掉。
        self._passes.pop(host, None)

        manual = self._manual_pass
        token = self._settings.safety.cf_clearance
        if manual is not None and self._manual_dead.get(host) != token:
            replayed = await self._replay(
                method, url, res_type, headers, jar, params, body, manual, announce=False
            )
            if replayed is not _MISS:
                self._passes[host] = manual
                logger.info("%s 手填的 cf_clearance 对 %s 有效，已启用", _LOG, host)
                return replayed
            self._manual_dead[host] = token
            logger.warning("%s %s", _LOG, self._manual_verdict(host))

        ticket = await self._solve(host, url)
        if ticket is None:
            return _MISS
        return await self._replay(method, url, res_type, headers, jar, params, body, ticket)

    async def _replay(
        self,
        method: str,
        url: str,
        res_type: ResType,
        headers: dict[str, str],
        jar: dict[str, str],
        params: dict[str, Any] | None,
        body: Any,
        ticket: Clearance,
        *,
        announce: bool = True,
    ) -> Any:
        """带着一张通行证重放原请求，没通过时返回哨兵。

        通行证的 User-Agent 必须覆盖原请求里的那个：cf_clearance 是连着 UA 一起
        签的，两者不一致等于没带。announce=False 用于「先试试看」的场合，让调用方
        自己写一条更准确的日志，避免同一次失败被解释两遍。
        """
        try:
            session = await self._ensure_session()
            async with session.request(
                method,
                url,
                headers={**headers, **ticket.headers},
                cookies={**jar, **ticket.cookies},
                params=params,
                json=body,
                proxy=self.proxy,
                allow_redirects=True,
            ) as response:
                if response.status >= 400:
                    if announce:
                        logger.warning(
                            "%s 带 cf_clearance 重放仍失败：%s 返回 %s", _LOG, url, response.status
                        )
                    return _MISS
                if res_type == "bytes":
                    return await response.read()
                text = await response.text()
                if looks_like_challenge(text):
                    if announce:
                        logger.warning("%s 带 cf_clearance 重放仍是校验页：%s", _LOG, url)
                    return _MISS
                return jsonlib.loads(text) if res_type == "json" else text
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            if announce:
                logger.warning("%s 带 cf_clearance 重放异常：%s", _LOG, error)
            return _MISS

    async def _solve(self, host: str, url: str) -> Clearance | None:
        """调用 FlareSolverr 取回 cf_clearance。

        FlareSolverr 内部跑的是真实浏览器，能把挑战里的 JavaScript 执行完，
        因此能拿到 TLS 指纹伪装永远拿不到的通行证。代价是它必须与本插件共用
        同一个出口 IP（通常部署在同一台机器上），否则拿回来的 cookie 无效。
        """
        endpoint = self._settings.safety.solver_url
        if not endpoint:
            return None

        base = endpoint.rstrip("/")
        payload = {"cmd": "request.get", "url": origin_of(url), "maxTimeout": _SOLVER_TIMEOUT_MS}
        logger.info("%s 正在请 FlareSolverr 通过 %s 的浏览器验证……", _LOG, host)
        try:
            session = await self._ensure_session()
            # 求解服务通常在本机，不能走站点代理。
            async with session.post(
                f"{base}/v1",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=_SOLVER_TIMEOUT_MS / 1000 + 30),
            ) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            logger.warning("%s FlareSolverr（%s）调用失败：%s", _LOG, base, error)
            return None

        solution = data.get("solution") if isinstance(data, Mapping) else None
        if not isinstance(solution, Mapping):
            logger.warning("%s FlareSolverr 返回结构异常：%s", _LOG, clip(str(data), 160))
            return None

        cookies = {
            str(item.get("name")): str(item.get("value"))
            for item in solution.get("cookies") or []
            if isinstance(item, Mapping) and item.get("name") and item.get("value")
        }
        token = cookies.get("cf_clearance")
        if not token:
            logger.warning("%s FlareSolverr 没取到 cf_clearance，%s 的验证未通过", _LOG, host)
            return None

        ticket = Clearance(
            cookies={"cf_clearance": token},
            user_agent=str(solution.get("userAgent") or ""),
            expire=time.monotonic() + _CLEARANCE_TTL,
        )
        self._passes[host] = ticket
        logger.info("%s 已取得 %s 的 cf_clearance，%s 分钟内复用", _LOG, host, _CLEARANCE_TTL // 60)
        return ticket

    async def _curl(
        self,
        method: str,
        url: str,
        res_type: ResType,
        headers: dict[str, str],
        cookies: dict[str, str] | None,
        params: dict[str, Any] | None,
        body: Any,
    ) -> Any:
        """用 curl_cffi 模拟真实浏览器的 TLS 指纹重试一次。

        只用于「疑似按客户端指纹拦截」的失败。JS 验证不会走到这里，因为指纹
        伪装解不了验证，白跑一次只是浪费时间。

        curl_cffi 用的是 requests 风格的 proxies 字典，与 aiohttp 不同，
        这里是唯一需要这种写法的地方。
        """
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning("%s 需要绕过风控但 curl_cffi 未安装，请执行 pip install curl_cffi", _LOG)
            return None

        proxy = self.proxy
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            async with curl_requests.AsyncSession(impersonate=self._settings.safety.tls) as session:
                response = await session.request(
                    method,
                    url,
                    headers=headers,
                    cookies=cookies,
                    params=params,
                    json=body,
                    proxies=proxies,
                    timeout=self._settings.basic.request_timeout,
                )
            if response.status_code >= 400:
                logger.warning(
                    "%s 浏览器指纹重试仍失败：%s 返回 %s", _LOG, url, response.status_code
                )
                return None
            if res_type == "bytes":
                return response.content
            text = response.text
            if looks_like_challenge(text):
                logger.warning("%s 浏览器指纹重试仍被风控拦截：%s", _LOG, url)
                return None
            return jsonlib.loads(text) if res_type == "json" else text
        except Exception as error:  # curl_cffi 的异常类型随版本变化，只能兜底
            logger.warning("%s 浏览器指纹重试异常：%s", _LOG, error)
            return None
