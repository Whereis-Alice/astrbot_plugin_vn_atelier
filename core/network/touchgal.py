"""TouchGal 站点客户端。

站点前置了 Cloudflare，普通 aiohttp 请求经常拿到校验页，因此所有请求都
打开 handle_cf，让 Http 在必要时用浏览器 TLS 指纹重试一次；遇到需要执行
JavaScript 的「Just a moment...」挑战页时由 Http 统一抛 ChallengeException，
调用方（如随机指令）可以据此切换到 VNDB 数据源。

站点根地址走配置而非硬编码常量：官方历史上从 touchgal.top 换到过
touchgal.ink，域名再变时用户改一个配置项就能继续用，不必等插件更新。
"""

from __future__ import annotations

import json as jsonlib

from ..settings import Settings
from ..types import (
    AuthorityException,
    CommandType,
    NoResultException,
    ResourceResponse,
    ResponseException,
    TouchGalResponse,
)
from ..utils import clip
from .http import Http, ua_for_profile

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
"""兜底 UA。默认与 TLS 指纹档位的默认值 chrome136 同版本：声明的浏览器版本
和握手指纹对不上，本身就是一条会被风控盯上的特征。"""

_NSFW_COOKIE = "kun-patch-setting-store|state|data|kunNsfwEnable"

_SEARCH_LIMIT = 12
"""站点搜索接口单页返回的条目数。"""


class TouchGal:
    """搜索、随机、详情页与下载资源。"""

    def __init__(self, http: Http, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    # -- 请求要素 ---------------------------------------------------------- #

    @property
    def base(self) -> str:
        """站点根地址，始终以 / 结尾。"""
        return self._settings.safety.touchgal_base

    @property
    def cooldown(self) -> int:
        """站点因浏览器验证被熔断的剩余秒数，0 表示可以正常请求。

        指令层可以先问一句再决定要不要发请求，省掉一次注定 403 的往返。
        """
        return self._http.challenged(self.base)

    @property
    def _user_agent(self) -> str:
        """本次请求声明的 User-Agent。

        优先用用户填的浏览器 UA：cf_clearance 与签发它的 UA 一起被签进签名，
        只有两者同源才有机会通过验证。没填时按配置的 TLS 指纹档位推一个同版本
        的 UA，避免出现「指纹说 Chrome 136、UA 说 Chrome 147」这种自相矛盾的组合。
        """
        safety = self._settings.safety
        return safety.browser_ua or ua_for_profile(safety.tls, _BROWSER_UA)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "user-agent": self._user_agent,
            "referer": self.base,
            "x-requested-with": "kun-fetch",
        }

    @property
    def _cookies(self) -> dict[str, str]:
        """站点自身需要的 Cookie。

        这里不放 cf_clearance：它由 Http 在真正命中验证时统一挂上并重放，验证
        通过后才缓存复用。放在这里会让一串早已失效的 cookie 跟着每个请求出门，
        既看不出它是死的，也让日志无法说清失败到底是哪一环。
        """
        safety = self._settings.safety
        cookies = {_NSFW_COOKIE: "all" if safety.enable_nsfw else "sfw"}
        if safety.touchgal_token:
            cookies["kun-galgame-patch-moe-token"] = safety.touchgal_token
        return cookies

    # -- 接口 -------------------------------------------------------------- #

    async def request_vn_by_search(
        self,
        command: CommandType,
        keyword: str,
        *,
        search_in_alias: bool = True,
        search_in_tag: bool = False,
        limit: int = _SEARCH_LIMIT,
        page: int = 1,
    ) -> tuple[list[TouchGalResponse], int]:
        """按关键词搜索作品，返回（结果列表，命中总数）。

        Args:
            command: 触发本次搜索的子指令，只用于异常文案。
            keyword: 空格分隔的关键词，会被拆成多个 keyword 条件。
            search_in_alias: 是否同时匹配别名。
            search_in_tag: 是否把关键词当作标签匹配（推荐指令用）。
            limit: 单页条目数。
            page: 页码，从 1 开始。
        """
        terms = [item for item in keyword.strip().split(" ") if item]
        payload = {
            "queryString": jsonlib.dumps(
                [{"type": "keyword", "name": item} for item in terms],
                ensure_ascii=False,
            ),
            "limit": limit,
            "searchOption": {
                "searchInIntroduction": False,
                "searchInAlias": search_in_alias,
                "searchInTag": search_in_tag,
            },
            "page": page,
            "selectedType": "all",
            "selectedLanguage": "all",
            "selectedPlatform": "all",
            "sortField": "resource_update_time",
            "sortOrder": "desc",
            "selectedYears": ["all"],
            "selectedMonths": ["all"],
        }
        url = f"{self.base}api/search/"
        response = await self._http.post(
            url,
            payload,
            res_type="json",
            headers=self._headers,
            cookies=self._cookies,
            handle_cf=True,
        )
        if not isinstance(response, dict):
            raise AuthorityException(clip(str(response), 120))

        games = response.get("galgames") or []
        total = int(response.get("total") or 0)
        if not games or total <= 0:
            raise NoResultException(command, keyword)
        return [TouchGalResponse.model_validate(item) for item in games], total

    async def request_random(self) -> str:
        """取一个随机作品的站内唯一标识。"""
        url = f"{self.base}api/home/random"
        response = await self._http.get(
            url,
            res_type="json",
            headers=self._headers,
            cookies=self._cookies,
            handle_cf=True,
        )
        unique_id = response.get("uniqueId") if isinstance(response, dict) else None
        if not unique_id:
            raise ResponseException(url)
        return str(unique_id)

    async def request_html(self, unique_id: str) -> str:
        """取详情页 HTML，交给 utils.html_parser 解析。"""
        url = f"{self.base}{unique_id}"
        html = await self._http.get(
            url,
            res_type="text",
            headers=self._headers,
            cookies=self._cookies,
            handle_cf=True,
        )
        if not html:
            raise ResponseException(url)
        return str(html)

    async def request_download(self, patch_id: int | str) -> list[ResourceResponse]:
        """取某个作品的下载资源列表。

        条目数上限在这里统一收口。上游把配置项声明成布尔值却拿去做切片，
        结果 True 只剩 1 条、False 直接剩 0 条，等于功能失效。
        """
        url = f"{self.base}api/patch/resource?patchId={patch_id}"
        response = await self._http.get(
            url,
            res_type="json",
            headers=self._headers,
            cookies=self._cookies,
            handle_cf=True,
        )
        if not isinstance(response, list):
            raise AuthorityException(clip(str(response), 120))

        resources = [ResourceResponse.model_validate(item) for item in response]
        if not resources:
            raise NoResultException(CommandType.DOWNLOAD, str(patch_id))
        limit = self._settings.basic.resource_limit
        return resources[:limit] if limit > 0 else resources
