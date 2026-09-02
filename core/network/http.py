"""统一的 HTTP 客户端。

与上游实现的三处差异，都是会直接导致请求失败的问题：

1. 代理参数写法。aiohttp 只认 proxy=，上游却传了 requests 风格的
   proxies={...}，抛出的 TypeError 被裸 except 吞掉，于是每个请求都要
   白跑满重试次数，TouchGal 实际上只能靠 curl_cffi 兜底才通。
2. 重试全程静默。上游只做 count += 1，出问题时日志里没有任何线索。
   现在每次失败都写 warning，并区分「可以重试」与「重试也没用」。
3. 从不检查状态码。403 返回的 HTML 挑战页会被直接当成 JSON 解析。
   现在先看状态码与响应体特征，命中风控立即切到 curl_cffi 重试一次。
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from typing import Any, Literal

import aiohttp
from astrbot.api import logger

from ..settings import Settings
from ..types import InternetException

ResType = Literal["text", "json", "bytes"]

_LOG = "[视觉小说工房]"

_JSON_HEADERS = {"Content-Type": "application/json"}

_CHALLENGE_STATUS = frozenset({403, 503})

_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "attention required!",
)


def looks_like_challenge(body: str) -> bool:
    """判断响应体是否是 Cloudflare 之类的人机校验页面。"""
    sample = body[:2048].lower()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


class Http:
    """带重试、风控识别与 curl_cffi 兜底的通用客户端。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

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
            handle_cf: 失败后是否用 curl_cffi 模拟浏览器指纹再试一次。
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

        merged = {**_JSON_HEADERS, **(headers or {})}
        retries = self._settings.basic.request_retries
        detail = "未知错误"

        for attempt in range(1, retries + 1):
            try:
                session = await self._ensure_session()
                async with session.request(
                    method,
                    url,
                    headers=merged,
                    cookies=cookies,
                    params=params,
                    json=body,
                    proxy=self.proxy,
                    allow_redirects=True,
                ) as response:
                    if response.status >= 400:
                        detail = f"HTTP {response.status}"
                        challenged = response.status in _CHALLENGE_STATUS
                        if challenged or response.status < 500:
                            logger.warning(
                                "%s %s 返回 %s，%s",
                                _LOG,
                                url,
                                response.status,
                                "疑似风控页，转用浏览器指纹重试"
                                if challenged
                                else "重试无意义，放弃",
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
                        return await response.read()
                    else:
                        text = await response.text()
                        if looks_like_challenge(text):
                            detail = "响应体是人机校验页"
                            logger.warning("%s %s 命中风控校验页", _LOG, url)
                            break
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

        if handle_cf:
            fallback = await self._curl(method, url, res_type, merged, cookies, params, body)
            if fallback is not None:
                return fallback

        logger.warning("%s 放弃请求 %s：%s", _LOG, url, detail)
        if err_handle is not None:
            return err_handle
        raise InternetException(url)

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
