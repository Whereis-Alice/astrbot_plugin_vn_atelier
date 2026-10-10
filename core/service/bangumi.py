"""有界缓存与跨站条目匹配。补充数据失败不会阻断原有卡片。"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..network.bangumi import Bangumi
from ..settings import Settings
from ..types import Tips, VndbVnResponse
from ..types.bangumi import BangumiSubject

_HOSTS = frozenset({"bgm.tv", "bangumi.tv", "chii.in", "www.bgm.tv", "www.bangumi.tv"})


def subject_id(value: str) -> int | None:
    text = value.strip()
    if len(text) <= 10 and text.isascii() and text.isdigit() and 0 < int(text) < 2**31:
        return int(text)
    if re.fullmatch(r"(?:bgm|bangumi)[:#]?[1-9]\d{0,8}", text, re.IGNORECASE):
        return int(re.search(r"\d+$", text)[0])  # type: ignore[index]
    try:
        url = urlsplit(text)
    except ValueError:
        return None
    if url.scheme in {"http", "https"} and url.hostname in _HOSTS:
        match = re.fullmatch(r"/subject/([1-9]\d{0,8})/?", url.path)
        if match:
            return int(match[1])
    return None


def title_key(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).casefold()
    return "".join(char for char in text if char.isalnum())


@dataclass(frozen=True, slots=True)
class BangumiMatch:
    subject: BangumiSubject | None = None
    method: str = ""
    status: str = "unmatched"


class BangumiData:
    def __init__(self, client: Bangumi, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._flights: dict[str, asyncio.Task[Any]] = {}

    async def terminate(self) -> None:
        tasks = list(self._flights.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._flights.clear()
        self._cache.clear()
        await self.client.terminate()

    async def _cached(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            self._cache.move_to_end(key)
            return cached[1]
        self._cache.pop(key, None)
        task = self._flights.get(key)
        if task is None:

            async def load() -> Any:
                try:
                    result = await fetch()
                    ttl = self.settings.bangumi.cache_hours * 3600
                    if result is None or result == []:
                        ttl = min(ttl, 1800)
                    if isinstance(result, BangumiMatch) and result.status != "matched":
                        ttl = min(ttl, 60 if result.status == "unavailable" else 1800)
                    self._cache[key] = (time.monotonic() + ttl, result)
                    while len(self._cache) > 256:
                        self._cache.popitem(last=False)
                    return result
                finally:
                    self._flights.pop(key, None)

            task = asyncio.create_task(load())
            self._flights[key] = task
            # A cancelled caller may leave the shared request running for another caller.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    async def subject(self, identifier: int) -> BangumiSubject | None:
        return await self._cached(f"subject:{identifier}", lambda: self.client.subject(identifier))

    async def search(self, keyword: str, *, limit: int | None = None) -> list[BangumiSubject]:
        count = limit or self.settings.bangumi.results
        return await self._cached(
            f"search:{keyword}:{count}", lambda: self.client.search(keyword, limit=count)
        )

    async def details(
        self, items: Sequence[BangumiSubject], *, strict: bool = False
    ) -> list[BangumiSubject]:
        async def one(item: BangumiSubject) -> BangumiSubject | None:
            try:
                return await self.subject(item.id)
            except Tips:
                if strict:
                    raise
                return item

        fetched = await asyncio.gather(*(one(item) for item in items))
        return [item for item in fetched if item is not None]

    async def ranking(self, year: int | None = None, tag: str = "") -> list[BangumiSubject]:
        # rank=0 means unranked; sort=rank can put these ahead of ranked subjects.
        # This command is deliberately a score-sorted list, not a copy of the site's rank.
        items = await self._cached(
            f"ranking:{year}:{tag}",
            lambda: self.client.search(
                "",
                sort="score",
                year=year,
                tag=tag,
                min_votes=self.settings.bangumi.min_votes,
                limit=self.settings.ranking.results,
            ),
        )
        return sorted(
            [
                item
                for item in items
                if item.rating.value is not None
                and item.rating.total >= self.settings.bangumi.min_votes
            ],
            key=lambda item: (item.rating.value or 0, item.rating.total),
            reverse=True,
        )

    async def match_vn(self, vn: VndbVnResponse) -> BangumiMatch:
        if not self.settings.bangumi.enabled or not self.settings.bangumi.enrich:
            return BangumiMatch(status="disabled")
        return await self._cached(f"match:{vn.id}", lambda: self._resolve(vn))

    async def _resolve(self, vn: VndbVnResponse) -> BangumiMatch:
        try:
            return await asyncio.wait_for(
                self._resolve_inner(vn), self.settings.bangumi.timeout + 4
            )
        except (Tips, asyncio.TimeoutError):
            return BangumiMatch(status="unavailable")

    async def _resolve_inner(self, vn: VndbVnResponse) -> BangumiMatch:
        explicit = {
            identifier
            for link in vn.extlinks
            if (identifier := subject_id(link.get("url", ""))) is not None
        }
        if len(explicit) > 1:
            return BangumiMatch(status="ambiguous")
        if explicit:
            item = await self.subject(next(iter(explicit)))
            if item and (not item.vndb_id or item.vndb_id == vn.id):
                return BangumiMatch(item, "条目外链", "matched")
            return BangumiMatch()

        names = [
            vn.alttitle or "",
            vn.title,
            *[t.title for t in vn.titles or []],
            *(vn.aliases or []),
        ]
        keys = {title_key(name) for name in names if title_key(name)}
        candidates: dict[int, BangumiSubject] = {}
        queries = list(dict.fromkeys(name for name in names if name.strip()))[:2]
        for query in queries:
            found = await self.search(query, limit=8)
            exact = [item for item in found if keys.intersection(map(title_key, item.names))]
            # Search responses omit aliases and external links; verify full subjects.
            for item in await self.details(exact[:4] if exact else found[:3], strict=True):
                candidates[item.id] = item
            linked = [item for item in candidates.values() if item.vndb_id == vn.id]
            if len(linked) == 1:
                return BangumiMatch(linked[0], "VNDB 外链", "matched")
            if len(linked) > 1 or len(exact) > 4:
                return BangumiMatch(status="ambiguous")
        linked = [item for item in candidates.values() if item.vndb_id == vn.id]
        if len(linked) == 1:
            return BangumiMatch(linked[0], "VNDB 外链", "matched")
        if len(linked) > 1:
            return BangumiMatch(status="ambiguous")
        exact = []
        for item in candidates.values():
            if item.vndb_id and item.vndb_id != vn.id:
                continue
            if not keys.intersection(map(title_key, item.names)):
                continue
            if vn.released and item.date and vn.released[:4] != item.date[:4]:
                continue
            exact.append(item)
        if len(exact) == 1:
            return BangumiMatch(exact[0], "精确标题", "matched")
        return BangumiMatch(status="ambiguous" if exact else "unmatched")

    async def match_title(self, title: str, external: Sequence[str] = ()) -> BangumiMatch:
        if not self.settings.bangumi.enabled or not self.settings.bangumi.enrich:
            return BangumiMatch(status="disabled")
        if not title_key(title) and not external:
            return BangumiMatch()
        try:
            return await self._cached(
                f"title:{title}:{tuple(external)}",
                lambda: asyncio.wait_for(
                    self._match_title_inner(title, external), self.settings.bangumi.timeout + 4
                ),
            )
        except (Tips, asyncio.TimeoutError):
            return BangumiMatch(status="unavailable")

    async def _match_title_inner(self, title: str, external: Sequence[str]) -> BangumiMatch:
        if len(external) >= 2 and external[0].lower().startswith("bangumi"):
            identifier = subject_id(external[1])
            if identifier:
                try:
                    item = await self.subject(identifier)
                    return BangumiMatch(item, "条目外链", "matched") if item else BangumiMatch()
                except Tips:
                    return BangumiMatch(status="unavailable")
        if not title_key(title):
            return BangumiMatch()
        try:
            items = await self.search(title)
            exact = [item for item in items if title_key(title) in map(title_key, item.names)]
            full = await self.details(exact, strict=True)
            if len(full) == 1:
                return BangumiMatch(full[0], "精确标题", "matched")
            return BangumiMatch(status="ambiguous" if full else "unmatched")
        except Tips:
            return BangumiMatch(status="unavailable")
