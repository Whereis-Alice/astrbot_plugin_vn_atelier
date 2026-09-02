"""TouchGal 站点客户端。

站点前置了 Cloudflare，普通 aiohttp 请求经常拿到校验页，因此所有请求都
打开 handle_cf，让 Http 在必要时用浏览器 TLS 指纹重试一次。
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
from .http import Http

_BASE = "https://www.touchgal.ink/"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

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
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "user-agent": _BROWSER_UA,
            "referer": _BASE,
            "x-requested-with": "kun-fetch",
        }

    @property
    def _cookies(self) -> dict[str, str]:
        safety = self._settings.safety
        cookies = {_NSFW_COOKIE: "all" if safety.enable_nsfw else "sfw"}
        if safety.touchgal_token:
            cookies["kun-galgame-patch-moe-token"] = safety.touchgal_token
        if safety.cf_clearance:
            cookies["cf_clearance"] = safety.cf_clearance
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
        url = f"{_BASE}api/search/"
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
        url = f"{_BASE}api/home/random"
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
        url = f"{_BASE}{unique_id}"
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
        url = f"{_BASE}api/patch/resource?patchId={patch_id}"
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
