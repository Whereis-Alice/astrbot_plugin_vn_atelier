"""Bangumi v0 API。只读取游戏条目，不抓网页、不修改用户收藏。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from ..settings import Settings
from ..types import Tips
from ..types.bangumi import BangumiSubject

_API = "https://api.bgm.tv/v0"
_UA = "Whereis-Alice/GalAtelier/4.0.1 (https://github.com/Whereis-Alice/astrbot_plugin_vn_atelier)"


class Bangumi:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._limit = asyncio.Semaphore(2)
        self._unavailable_until = 0.0

    async def terminate(self) -> None:
        if self._session is not None:
            await self._session.close()
        self._session = None

    async def _request(
        self, path: str, *, body: dict[str, Any] | None = None, limit: int = 8
    ) -> Any:
        if not self._settings.bangumi.enabled:
            raise Tips("Bangumi 数据源已关闭，请在插件配置中开启。")
        async with self._limit:
            if time.monotonic() < self._unavailable_until:
                raise Tips("Bangumi 接口暂不可用或正在限流，请稍后再试。")
            if self._session is None or self._session.closed:
                headers = {"User-Agent": _UA, "Accept": "application/json"}
                if self._settings.bangumi.token:
                    headers["Authorization"] = f"Bearer {self._settings.bangumi.token}"
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._settings.bangumi.timeout),
                    headers=headers,
                )
            try:
                async with self._session.request(
                    "POST" if body is not None else "GET",
                    _API + path,
                    params={"limit": limit, "offset": 0} if body is not None else None,
                    json=body,
                    proxy=self._settings.safety.proxy or None,
                    allow_redirects=False,
                ) as response:
                    if response.status == 404:
                        return None
                    if response.status == 429:
                        raw = response.headers.get("Retry-After", "60")
                        delay = min(300, max(10, int(raw))) if raw.isdigit() else 60
                        self._unavailable_until = time.monotonic() + delay
                        raise Tips("Bangumi 接口正在限流，请稍后再试。")
                    if response.status >= 400 or 300 <= response.status < 400:
                        self._unavailable_until = time.monotonic() + 30
                        raise Tips(
                            f"Bangumi 接口返回 HTTP {response.status}，请检查网络或访问令牌。"
                        )
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                self._unavailable_until = time.monotonic() + 30
                raise Tips("Bangumi 接口暂不可用，未能取得数据。") from error

    def _visible(self, subject: BangumiSubject) -> bool:
        return subject.type == 4 and (not subject.nsfw or self._settings.safety.enable_nsfw)

    async def subject(self, subject_id: int) -> BangumiSubject | None:
        raw = await self._request(f"/subjects/{int(subject_id)}")
        if not isinstance(raw, dict):
            return None
        try:
            subject = BangumiSubject.model_validate(raw)
        except ValueError as error:
            raise Tips("Bangumi 返回了无法解析的条目数据。") from error
        return subject if self._visible(subject) else None

    async def search(
        self,
        keyword: str,
        *,
        limit: int = 8,
        sort: str = "match",
        year: int | None = None,
        tag: str = "",
        min_votes: int = 0,
    ) -> list[BangumiSubject]:
        filters: dict[str, Any] = {"type": [4]}
        # Bangumi 的 filter.nsfw 必须是布尔值；默认排除成人条目，
        # 仅在开启 NSFW 且填写账号令牌时省略该过滤以纳入成人条目。
        if not (self._settings.safety.enable_nsfw and self._settings.bangumi.token):
            filters["nsfw"] = False
        if year:
            filters["air_date"] = [f">={year}-01-01", f"<{year + 1}-01-01"]
        if tag:
            filters["tag"] = [tag]
        if min_votes:
            filters["rating_count"] = [f">={min_votes}"]
        raw = await self._request(
            "/search/subjects",
            body={"keyword": keyword, "sort": sort, "filter": filters},
            limit=max(1, min(limit, 24)),
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
            raise Tips("Bangumi 返回了无法解析的搜索数据。")
        subjects = []
        for item in raw["data"]:
            try:
                subject = BangumiSubject.model_validate(item)
            except (ValueError, TypeError):
                continue
            if self._visible(subject):
                subjects.append(subject)
        return subjects
