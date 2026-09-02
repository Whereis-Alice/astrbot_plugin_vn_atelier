"""VNDB Kana API 客户端。

VNDB 的查询都是「POST 一个 filters + fields 的 JSON」，因此这里只维护
两个底层方法：_results（空结果视为异常）与 _results_or_empty（空结果视为
正常），其余方法都只负责拼 payload 与转模型。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
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
"""简讯每类最多展示的条目数。上游没传 results，只能拿到默认的 10 条。"""

_BIRTHDAY_POOL = 40
"""每日推送挑选生日角色时的候选池大小，之后再按性别在本地筛选。"""

_MAX_RESULTS = 100
"""VNDB 单次查询的结果上限。"""


class Vndb:
    """作品 / 角色 / 厂商 / 标签查询。"""

    def __init__(self, http: Http, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    # -- 底层 -------------------------------------------------------------- #

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{_KANA}{endpoint}"
        response = await self._http.post(url, payload, res_type="json")
        if not isinstance(response, dict):
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

    # -- 纪念日 / 每日推送 -------------------------------------------------- #

    def _released_filters(self, date: Sequence[str]) -> list[list[Any]]:
        """构造「历年同月同日」的发布日期过滤器。

        上游写死了 range(1990, int(date[0]))：起始年份不可配置，而且右开
        区间把当年整个排除掉了，今年发售的作品永远上不了纪念日。
        """
        year = int(date[0])
        start = min(self._settings.event.start_year, year)
        return [["released", "=", f"{item}-{date[1]}-{date[2]}"] for item in range(start, year + 1)]

    async def request_by_event(
        self, date: Sequence[str]
    ) -> tuple[list[VndbVnResponse], list[VndbCharacterResponse]]:
        """查询某天的「发售纪念日作品」与「生日角色」。"""
        released = self._released_filters(date)
        rating = self._settings.event.rating
        vn_payload = {
            "filters": ["and", ["or", *released], ["rating", ">=", rating]],
            "fields": VNDB_FIELDS["vn_short"],
            "sort": "rating",
            "reverse": True,
            "results": _EVENT_RESULTS,
        }
        cha_payload = {
            "filters": [
                "and",
                ["birthday", "=", [int(date[1]), int(date[2])]],
                ["vn", "=", ["rating", ">=", rating]],
            ],
            "fields": VNDB_FIELDS["character_event"],
            "results": _EVENT_RESULTS,
        }
        vn_raw, cha_raw = await asyncio.gather(
            self._results_or_empty("vn", vn_payload),
            self._results_or_empty("character", cha_payload),
        )
        vns = [VndbVnResponse.model_validate(item) for item in vn_raw]
        characters = [VndbCharacterResponse.model_validate(item) for item in cha_raw]
        if not vns and not characters:
            raise NoResultException(CommandType.EVENT, "-".join(date))
        return vns, characters

    async def request_by_event_vn(
        self, date: Sequence[str]
    ) -> tuple[VndbVnResponse, list[VndbCharacterResponse]]:
        """挑出当天最值得推送的一部纪念日作品，并附带其主要角色。"""
        released = self._released_filters(date)
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
    ) -> tuple[VndbCharacterResponse, list[VndbVnResponse]]:
        """挑出当天最值得推送的一位生日角色，并附带其全部出场作品。

        上游这段有两个硬伤：for 循环里 return、却又指望 else 分支去抛
        「检索失败」，结果 else 永远不会执行；以及直接遍历 character.vns，
        VNDB 返回 null 时抛 TypeError。这里改成显式的映射查找。
        """
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

        owner_of_vn: dict[str, str] = {}
        for character in characters:
            for vn in character.vns or []:
                owner_of_vn.setdefault(vn.id, character.id)
        if not owner_of_vn:
            raise NoResultException(CommandType.EVENT_TIMED, stamp)

        content = self._settings.schedule.content
        scope: list[Any] = ["or", *[["id", "=", item] for item in owner_of_vn]]
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

        chosen_id = owner_of_vn.get(str(best[0].get("id") or ""))
        chosen = next((item for item in characters if item.id == chosen_id), None)
        if chosen is None:
            raise NoResultException(CommandType.EVENT_TIMED, stamp)

        own_vns = [key for key, value in owner_of_vn.items() if value == chosen.id]
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
        return chosen, [VndbVnResponse.model_validate(item) for item in vns_raw]
