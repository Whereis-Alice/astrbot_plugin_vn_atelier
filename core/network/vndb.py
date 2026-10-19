"""VNDB Kana API 客户端。

VNDB 的查询都是「POST 一个 filters + fields 的 JSON」，因此这里只维护
两个底层方法：_results（空结果视为异常）与 _results_or_empty（空结果视为
正常），其余方法都只负责拼 payload 与转模型。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from datetime import date as Date
from typing import Any

from ..settings import Settings
from ..types import (
    VNDB_FIELDS,
    CommandType,
    NoResultException,
    ResponseException,
    VndbCharacterResponse,
    VndbProducerResponse,
    VndbVnResponse,
)
from .http import Http

_KANA = "https://api.vndb.org/kana/"

_EVENT_RESULTS = 12
"""精选卡附属作品、登场角色的条目数；独立清单数量由配置控制。"""

_BIRTHDAY_POOL = 40
"""每日推送挑选生日角色时的候选池大小，之后再按性别在本地筛选。"""

_MAX_RESULTS = 100
"""VNDB 单次查询的结果上限。"""

_RANDOM_PAGE_SIZE = 25
_RANDOM_PAGES = 40
"""随机抽取的候选池：25 × 40 = 前 1000 部热门作品，VNDB 允许翻到这个深度。"""

_RANDOM_MIN_VOTES = 50
"""随机抽取的投票数下限，避免抽到只有个位数评价的冷门条目。"""


class Vndb:
    """作品 / 角色 / 厂商 / 标签查询。"""

    def __init__(self, http: Http, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    # -- 底层 -------------------------------------------------------------- #

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{_KANA}{endpoint}"
        response = await self._http.post(url, payload, res_type="json")
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("results"), list)
            or any(not isinstance(item, dict) for item in response["results"])
        ):
            raise ResponseException(url)
        return response

    async def _results(
        self, endpoint: str, payload: dict[str, Any], command: CommandType, value: str
    ) -> list[dict[str, Any]]:
        """查询并要求至少命中一条，否则抛 NoResultException。"""
        results = (await self._post(endpoint, payload)).get("results") or []
        if not results:
            raise NoResultException(command, value)
        return results

    async def _results_or_empty(
        self, endpoint: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """查询并允许空结果，由调用方决定怎么处理。"""
        return (await self._post(endpoint, payload)).get("results") or []

    # -- 关键词检索 -------------------------------------------------------- #

    async def request_by_vn(
        self, keyword: str, payload: dict[str, Any] | None = None
    ) -> list[VndbVnResponse]:
        """按关键词（或自定义 payload）检索作品。"""
        body = payload or {
            "filters": ["search", "=", keyword],
            "fields": VNDB_FIELDS["vn"],
        }
        results = await self._results("vn", body, CommandType.VN, keyword)
        return [VndbVnResponse.model_validate(item) for item in results]

    async def request_by_character(
        self, keyword: str, payload: dict[str, Any] | None = None
    ) -> list[VndbCharacterResponse]:
        """按关键词检索角色。"""
        body = payload or {
            "filters": ["search", "=", keyword],
            "fields": VNDB_FIELDS["character"],
        }
        results = await self._results("character", body, CommandType.CHARACTER, keyword)
        return [VndbCharacterResponse.model_validate(item) for item in results]

    async def request_by_producer(
        self, keyword: str, payload: dict[str, Any] | None = None
    ) -> tuple[list[VndbProducerResponse], list[list[VndbVnResponse]]]:
        """检索厂商，并为每个厂商附带一份按评分排序的代表作列表。"""
        body = payload or {
            "filters": ["search", "=", keyword],
            "fields": VNDB_FIELDS["producer"],
        }
        results = await self._results("producer", body, CommandType.PRODUCER, keyword)
        producers = [VndbProducerResponse.model_validate(item) for item in results]

        count = self._settings.producer.producer_vns or _MAX_RESULTS
        groups = await asyncio.gather(
            *(
                self._results_or_empty(
                    "vn",
                    {
                        "filters": ["developer", "=", ["id", "=", producer.id]],
                        "fields": VNDB_FIELDS["vn_short"],
                        "sort": "rating",
                        "reverse": True,
                        "results": count,
                    },
                )
                for producer in producers
            )
        )
        return producers, [
            [VndbVnResponse.model_validate(item) for item in group] for group in groups
        ]

    async def request_by_id(self, real_type: CommandType, keyword: str) -> Any:
        """按 VNDB ID 精确查询，返回值形态与对应的关键词检索方法一致。"""
        payload = {
            "filters": ["id", "=", keyword],
            "fields": VNDB_FIELDS[real_type.value],
        }
        handlers = {
            CommandType.VN: self.request_by_vn,
            CommandType.CHARACTER: self.request_by_character,
            CommandType.PRODUCER: self.request_by_producer,
        }
        try:
            return await handlers[real_type](keyword, payload)
        except NoResultException as error:
            raise NoResultException(CommandType.ID, keyword) from error

    async def request_by_find(self, character: str, vn: str) -> VndbCharacterResponse | None:
        """按「角色名 + 作品名」精确定位一个角色，找不到返回 None。"""
        payload = {
            "filters": ["and", ["search", "=", character], ["vn", "=", ["search", "=", vn]]],
            "fields": VNDB_FIELDS["character_short"],
            "results": 1,
        }
        results = await self._results_or_empty("character", payload)
        return VndbCharacterResponse.model_validate(results[0]) if results else None

    # -- 排行榜 ------------------------------------------------------------ #

    async def resolve_tag(self, name: str) -> tuple[str, str]:
        """把标签名解析成 VNDB 标签 ID。

        VNDB 的 tag 过滤器只接受标签 ID，所以先查一次 /tag 拿 ID，
        顺便把官方写法回传给调用方用于标题展示。
        """
        results = await self._results(
            "tag",
            {"filters": ["search", "=", name], "fields": "id,name", "results": 1},
            CommandType.RANKING,
            name,
        )
        first = results[0]
        return str(first.get("id") or ""), str(first.get("name") or name)

    async def request_ranking(
        self,
        *,
        year: int | None = None,
        tag_id: str | None = None,
        label: str = "全站",
    ) -> list[VndbVnResponse]:
        """按贝叶斯评分取排行榜，可选按年份或标签收窄范围。"""
        conditions: list[Any] = [["votecount", ">=", self._settings.ranking.min_votes]]
        if year is not None:
            conditions.append(["released", ">=", f"{year}-01-01"])
            conditions.append(["released", "<=", f"{year}-12-31"])
        if tag_id:
            conditions.append(["tag", "=", tag_id])
        payload = {
            "filters": ["and", *conditions],
            "fields": VNDB_FIELDS["vn_short"],
            "sort": "rating",
            "reverse": True,
            "results": self._settings.ranking.results,
        }
        results = await self._results("vn", payload, CommandType.RANKING, label)
        return [VndbVnResponse.model_validate(item) for item in results]

    # -- 随机抽取 ---------------------------------------------------------- #

    async def request_random_vn(self) -> VndbVnResponse:
        """随机抽一部有一定知名度的作品。

        VNDB 没有随机接口，这里的做法是先用投票数门槛滤掉几乎没人玩过的
        条目，再在「按投票数排序的前若干页」里随机翻一页、随机取一条。
        投票数门槛顺带把翻页深度限制在 VNDB 允许的范围内。

        这条链路是随机指令的备选数据源：TouchGal 开启浏览器验证、或干脆
        换了域名时，指令仍然能出结果，而不是直接报错。
        """
        votes = max(self._settings.ranking.min_votes, _RANDOM_MIN_VOTES)
        payload: dict[str, Any] = {
            "filters": ["votecount", ">=", votes],
            "fields": VNDB_FIELDS["vn"],
            "sort": "votecount",
            "reverse": True,
            "results": _RANDOM_PAGE_SIZE,
            "page": random.randint(1, _RANDOM_PAGES),
            "count": True,
        }
        data = await self._post("vn", payload)
        results = data.get("results") or []

        if not results:
            # 用户把投票门槛调得很高时候选可能不足 40 页，按实际总数重挑一页。
            total = int(data.get("count") or 0)
            pages = max(1, min(_RANDOM_PAGES, -(-total // _RANDOM_PAGE_SIZE)))
            payload["page"] = random.randint(1, pages)
            payload.pop("count", None)
            results = await self._results_or_empty("vn", payload)

        if not results:
            raise NoResultException(CommandType.RANDOM, "随机作品")
        return VndbVnResponse.model_validate(random.choice(results))

    # -- 纪念日 / 每日推送 -------------------------------------------------- #

    def _released_filters(self, date: Sequence[str]) -> list[list[Any]]:
        """只查往年首发，今日首发由新作栏目处理；跳过平年的 2 月 29 日。"""
        year = int(date[0])
        start = min(self._settings.event.start_year, year)
        filters = []
        for item in range(start, year):
            try:
                stamp = Date(item, int(date[1]), int(date[2])).isoformat()
            except ValueError:
                continue
            filters.append(["released", "=", stamp])
        return filters

    async def request_anniversaries(
        self, date: Sequence[str], *, limit: int = 12
    ) -> list[VndbVnResponse]:
        """纪念日清单不沿用精选的评分、热度门槛。"""
        released = self._released_filters(date)
        if not released:
            return []
        raw = await self._results_or_empty(
            "vn",
            {
                "filters": ["or", *released],
                "fields": VNDB_FIELDS["vn"],
                "sort": "rating",
                "reverse": True,
                "results": max(1, min(limit, _MAX_RESULTS)),
            },
        )
        return [VndbVnResponse.model_validate(item) for item in raw]

    async def request_birthdays(
        self, date: Sequence[str], *, limit: int = 12, exclude: Sequence[str] = ()
    ) -> list[VndbCharacterResponse]:
        """生日清单不限制性别、角色戏份或关联作品评分；排除已推送寿星后再取数量。"""
        conditions: list[Any] = [["birthday", "=", [int(date[1]), int(date[2])]]]
        conditions.extend(["id", "!=", item] for item in dict.fromkeys(exclude))
        raw = await self._results_or_empty(
            "character",
            {
                "filters": ["and", *conditions],
                "fields": VNDB_FIELDS["character_event"],
                "sort": "id",
                "results": max(1, min(limit, _MAX_RESULTS)),
            },
        )
        return [VndbCharacterResponse.model_validate(item) for item in raw]

    async def request_new_releases(
        self, date: Sequence[str], *, limit: int = 12
    ) -> list[VndbVnResponse]:
        """按 VN 条目的首次发售日查新作，不查移植/翻译等 release 记录。"""
        stamp = Date(*(int(part) for part in date)).isoformat()
        raw = await self._results_or_empty(
            "vn",
            {
                "filters": ["released", "=", stamp],
                "fields": VNDB_FIELDS["vn"],
                "sort": "votecount",
                "reverse": True,
                "results": max(1, min(limit, _MAX_RESULTS)),
            },
        )
        return [VndbVnResponse.model_validate(item) for item in raw]

    async def request_by_event(
        self, date: Sequence[str]
    ) -> tuple[list[VndbVnResponse], list[VndbCharacterResponse]]:
        """兼容入口：清单不再继承精选质量门槛，数量由每日清单设置决定。"""
        vns, characters = await asyncio.gather(
            self.request_anniversaries(date, limit=self._settings.schedule.anniversary_count),
            self.request_birthdays(date, limit=self._settings.schedule.birthday_count),
        )
        if not vns and not characters:
            raise NoResultException(CommandType.EVENT, "-".join(date))
        return vns, characters

    async def request_by_event_vn(
        self, date: Sequence[str]
    ) -> tuple[VndbVnResponse, list[VndbCharacterResponse]]:
        """挑出当天最值得推送的一部纪念日作品，并附带其主要角色。"""
        released = self._released_filters(date)
        if not released:
            raise NoResultException(CommandType.EVENT_TIMED, "/".join(date))
        content = self._settings.schedule.content
        scope: list[Any] = ["or", *released]
        payload = {
            "filters": scope if content != "c" else ["and", scope, ["votecount", ">", 50]],
            "fields": VNDB_FIELDS["vn"],
            "results": 1,
            "sort": "votecount" if content == "b" else "rating",
            "reverse": True,
        }
        results = await self._results_or_empty("vn", payload)
        if not results:
            raise NoResultException(CommandType.EVENT_TIMED, "/".join(date))
        vn = VndbVnResponse.model_validate(results[0])

        characters = await self._results_or_empty(
            "character",
            {
                "filters": [
                    "and",
                    ["vn", "=", ["id", "=", vn.id]],
                    ["or", ["role", "=", "main"], ["role", "=", "primary"]],
                ],
                "fields": VNDB_FIELDS["character_event"],
                "results": _EVENT_RESULTS,
            },
        )
        return vn, [VndbCharacterResponse.model_validate(item) for item in characters]

    async def request_by_event_cha(
        self, date: Sequence[str]
    ) -> tuple[list[VndbCharacterResponse], list[VndbVnResponse]]:
        """先按原有口味选作品，再取该作品全部同日寿星，避免同作漏人。"""
        stamp = "/".join(date)
        month, day = int(date[1]), int(date[2])
        raw = await self._results_or_empty(
            "character",
            {
                "filters": [
                    "and",
                    ["birthday", "=", [month, day]],
                    ["vn", "=", ["rating", ">=", self._settings.event.rating]],
                    ["or", ["role", "=", "main"], ["role", "=", "primary"]],
                ],
                "fields": VNDB_FIELDS["character"],
                "results": _BIRTHDAY_POOL,
            },
        )
        characters = [VndbCharacterResponse.model_validate(item) for item in raw]

        # 性别筛选放在本地做：VNDB 历史上把 gender 字段改名成了 sex，
        # 过滤器名称在不同版本间并不稳定，本地筛选永远不会因此报错。
        wanted = {"a": "f", "b": "m"}.get(self._settings.schedule.gender_filter)
        if wanted:
            filtered = [item for item in characters if item.sex and item.sex[0] == wanted]
            characters = filtered or characters

        candidate_vns = {vn.id for character in characters for vn in character.vns or []}
        if not candidate_vns:
            raise NoResultException(CommandType.EVENT_TIMED, stamp)

        content = self._settings.schedule.content
        scope: list[Any] = ["or", *[["id", "=", item] for item in sorted(candidate_vns)]]
        best = await self._results_or_empty(
            "vn",
            {
                "filters": scope if content != "c" else ["and", scope, ["votecount", ">", 50]],
                "fields": "id",
                "results": 1,
                "sort": "votecount" if content == "b" else "rating",
                "reverse": True,
            },
        )
        if not best:
            raise NoResultException(CommandType.EVENT_TIMED, stamp)

        selected_vn = str(best[0].get("id") or "")
        chosen: dict[str, VndbCharacterResponse] = {}
        # 内部分页只用于补齐同作寿星，不影响用户端「一张图、不翻页」。
        for page in range(1, 11):
            response = await self._post(
                "character",
                {
                    "filters": [
                        "and",
                        ["birthday", "=", [month, day]],
                        ["vn", "=", ["id", "=", selected_vn]],
                    ],
                    "fields": VNDB_FIELDS["character"],
                    "sort": "id",
                    "results": _MAX_RESULTS,
                    "page": page,
                },
            )
            for item in response.get("results") or []:
                character = VndbCharacterResponse.model_validate(item)
                chosen[character.id] = character
            if not response.get("more"):
                break
        else:
            raise ResponseException("同作生日角色超过查询安全上限")
        if not chosen:
            raise NoResultException(CommandType.EVENT_TIMED, stamp)

        own_vns = list(
            dict.fromkeys(
                [
                    selected_vn,
                    *[vn.id for character in chosen.values() for vn in character.vns or []],
                ]
            )
        )
        vns_raw = await self._results_or_empty(
            "vn",
            {
                "filters": ["or", *[["id", "=", item] for item in own_vns]],
                "fields": VNDB_FIELDS["vn_short"],
                "sort": "rating",
                "reverse": True,
                "results": _EVENT_RESULTS,
            },
        )
        vns = [VndbVnResponse.model_validate(item) for item in vns_raw]
        vns.sort(key=lambda vn: vn.id != selected_vn)
        return list(chosen.values()), vns
