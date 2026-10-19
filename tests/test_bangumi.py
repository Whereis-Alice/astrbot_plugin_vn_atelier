"""Bangumi 离线回归测试：python tests/test_bangumi.py（也支持 pytest）。

所有作品与评分都是合成数据；HTTP 测试只连接本机临时端口。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from jinja2 import Environment

ROOT = Path(__file__).resolve().parent.parent
PKG = "vn_atelier_selftest"
if PKG not in sys.modules:
    package = types.ModuleType(PKG)
    package.__path__ = [str(ROOT)]
    sys.modules[PKG] = package


def module(name):
    return importlib.import_module(PKG + ".core." + name)


Settings = module("settings").Settings
network = module("network.bangumi")
service = module("service.bangumi")
BangumiData = service.BangumiData
BangumiMatch = service.BangumiMatch
BangumiSubject = module("types.bangumi").BangumiSubject
BangumiRating = module("types.bangumi").BangumiRating
VndbVnResponse = module("types").VndbVnResponse
Tips = module("types").Tips
Layout = module("types").Layout
Theme = module("types").Theme
Renderer = module("service.renderer").Renderer
utils = module("utils.bangumi")


def subject(identifier=13, **changes):
    data = {
        "id": identifier,
        "type": 4,
        "name": "星空航迹",
        "date": "2024-04-28",
        "summary": "虚构作品的测试简介。",
        "rating": {"score": 8.2, "total": 120, "rank": 50},
    }
    data.update(changes)
    return BangumiSubject.model_validate(data)


def vn(**changes):
    return VndbVnResponse.model_validate(
        {
            "id": "v4",
            "title": "星空航迹",
            "released": "2024-04-28",
            "rating": 82.3,
            "votecount": 1000,
            **changes,
        }
    )


class ModelTests(unittest.TestCase):
    def test_ids_and_trusted_links(self):
        for value in (
            "13",
            "bgm:13",
            "Bangumi#13",
            "https://bgm.tv/subject/13?x=1",
            "https://chii.in/subject/13/",
        ):
            self.assertEqual(service.subject_id(value), 13)
        for value in (
            "0",
            "v13",
            "https://bgm.tv.evil/subject/13",
            "https://evil/subject/13",
            "https://bgm.tv/subject/13/other",
            "file://bgm.tv/subject/13",
        ):
            self.assertIsNone(service.subject_id(value))

    def test_missing_rating_is_not_zero_score(self):
        for rating in (
            {},
            {"score": 8.2, "total": 0},
            {"score": None, "total": None, "rank": None},
            {"score": float("nan"), "total": 5},
            {"score": 11, "total": 5},
        ):
            item = subject(rating=rating)
            self.assertIsNone(item.rating.value)
            self.assertIn("暂无评分", "\n".join(utils.bangumi_lines(item)))
            self.assertEqual(utils.rating_data(item)[0]["score"], "暂无")
        self.assertEqual(BangumiRating(score=8.2, total=120).value, 8.2)

    def test_nulls_aliases_and_external_link(self):
        item = subject(
            images=None,
            collection=None,
            tags=None,
            summary=None,
            infobox=[
                {"key": "别名", "value": [{"k": "日文", "v": "Starlight"}]},
                {"key": "链接", "value": "https://vndb.org/v4"},
            ],
        )
        self.assertIn("Starlight", item.names)
        self.assertEqual(item.vndb_id, "v4")
        self.assertEqual(vn(extlinks=None).extlinks, [])
        self.assertEqual(
            vn(
                extlinks=[None, {"url": None}, {"url": "https://bgm.tv/subject/13", "label": None}]
            ).extlinks,
            [{"url": "https://bgm.tv/subject/13"}],
        )

    def test_title_normalization_does_not_strip_sequels(self):
        self.assertEqual(service.title_key("ＳＴＡＲ-Light!"), service.title_key("star light"))
        self.assertNotEqual(service.title_key("星空航迹"), service.title_key("星空航迹 2"))

    def test_two_scales_and_distribution(self):
        ratings = utils.rating_data(subject(), vn())
        self.assertEqual(
            [(r["source"], r["scale"]) for r in ratings], [("Bangumi", "/10"), ("VNDB", "/100")]
        )
        item = subject(
            rating={
                "score": 8.2,
                "total": 120,
                "count": {"1": 0, "9": 10, "10": 20, "99": 7, "2": -3},
            }
        )
        bins = utils.distribution_data(item)
        self.assertEqual(len(bins), 10)
        self.assertEqual(bins[8]["height"], 45)
        self.assertEqual(bins[9]["height"], 90)

    def test_unrated_vndb_and_oversized_id(self):
        self.assertEqual(utils.rating_data(vn=vn(rating=0, votecount=0))[0]["score"], "暂无")
        self.assertIsNone(service.subject_id("9" * 5000))

    def test_config_clamps_and_defaults(self):
        default = Settings.from_config({})
        self.assertTrue(default.bangumi.enabled)
        self.assertTrue(default.bangumi.enrich)
        settings = Settings.from_config(
            {
                "bangumiSetting": {
                    "timeout": -1,
                    "cacheHours": 999,
                    "results": "bad",
                    "minVotes": -10,
                }
            }
        )
        self.assertEqual(
            (
                settings.bangumi.timeout,
                settings.bangumi.cache_hours,
                settings.bangumi.results,
                settings.bangumi.min_votes,
            ),
            (3, 48, 8, 0),
        )

    def test_templates_all_themes_escape_and_display_scores(self):
        base = module("command.base")
        item = subject(name='<script>alert("x")</script>')
        data = base.feature(
            main_image="",
            subtitle=item.name,
            info=utils.bangumi_lines(item),
            ratings=utils.rating_data(item, vn()),
            distribution=utils.distribution_data(item),
        )
        for theme in Theme:
            settings = Settings()
            settings.render.theme = theme
            renderer = Renderer(settings, ROOT / "resources")
            payload = renderer.prepare(data)
            payload.update(renderer._common())
            for autoescape in (False, True):
                page = (
                    Environment(autoescape=autoescape)
                    .from_string(renderer.template(Layout.FEATURE))
                    .render(**payload)
                )
                self.assertIn("Bangumi", page)
                self.assertIn("/10", page)
                self.assertIn("/100", page)
                self.assertEqual(page.count('class="distribution__bin"'), 10)
                self.assertNotIn('<script>alert("x")</script>', page)
            text = renderer.plain_text(data)
            self.assertIn("Bangumi 评分：8.2/10", text)
            self.assertIn("VNDB 评分", text)


class MatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings()
        self.client = types.SimpleNamespace(
            subject=AsyncMock(), search=AsyncMock(), terminate=AsyncMock()
        )
        self.data = BangumiData(self.client, self.settings)

    async def asyncTearDown(self):
        await self.data.terminate()

    async def test_explicit_link_takes_precedence(self):
        self.client.subject.return_value = subject(name="原文名称")
        result = await self.data.match_vn(vn(extlinks=[{"url": "https://bgm.tv/subject/13"}]))
        self.assertEqual(result.method, "条目外链")
        self.client.search.assert_not_awaited()

    async def test_multiple_links_and_conflicting_backlink(self):
        result = await self.data.match_vn(
            vn(
                extlinks=[
                    {"url": "https://bgm.tv/subject/13"},
                    {"url": "https://bgm.tv/subject/14"},
                ]
            )
        )
        self.assertEqual(result.status, "ambiguous")
        self.client.subject.return_value = subject(
            infobox=[{"key": "链接", "value": "https://vndb.org/v99"}]
        )
        result = await self.data.match_vn(
            vn(id="v5", extlinks=[{"url": "https://bgm.tv/subject/13"}])
        )
        self.assertIsNone(result.subject)

    async def test_reverse_vndb_link_is_authoritative(self):
        item = subject(
            name="不同译名",
            date="2020-01-01",
            infobox=[{"key": "VNDB", "value": "https://vndb.org/v4"}],
        )
        self.client.search.return_value = [item]
        self.client.subject.return_value = item
        result = await self.data.match_vn(vn())
        self.assertEqual(result.method, "VNDB 外链")

    async def test_exact_title_and_alias(self):
        item = subject(name="Starlight", infobox=[{"key": "别名", "value": [{"v": "星空航迹"}]}])
        self.client.search.return_value = [item]
        self.client.subject.return_value = item
        result = await self.data.match_vn(vn())
        self.assertEqual(result.method, "精确标题")

    async def test_year_and_sequel_and_conflicting_link_rejected(self):
        for item in (
            subject(date="2025-01-01"),
            subject(name="星空航迹 2"),
            subject(infobox=[{"key": "VNDB", "value": "https://vndb.org/v99"}]),
        ):
            await self.data.terminate()
            self.client.search.return_value = [item]
            self.client.subject.return_value = item
            result = await self.data.match_vn(vn())
            self.assertIsNone(result.subject)

    async def test_ambiguous_titles_not_first_search_hit(self):
        first, second = subject(13), subject(14)
        self.client.search.return_value = [first, second]
        self.client.subject.side_effect = lambda identifier: first if identifier == 13 else second
        result = await self.data.match_vn(vn())
        self.assertEqual(result.status, "ambiguous")

    async def test_failed_details_do_not_trust_search_stub(self):
        self.client.search.return_value = [subject()]
        self.client.subject.side_effect = Tips("详情不可用")
        for result in (await self.data.match_vn(vn()), await self.data.match_title("星空航迹")):
            self.assertEqual(result.status, "unavailable")
            self.assertIsNone(result.subject)

    async def test_empty_title_never_searches_all_games(self):
        self.assertEqual((await self.data.match_title("  ")).status, "unmatched")
        self.client.search.assert_not_awaited()

    async def test_disabled_enrichment_no_network(self):
        self.settings.bangumi.enrich = False
        self.assertEqual((await self.data.match_vn(vn())).status, "disabled")
        self.client.search.assert_not_awaited()

    async def test_timeout_yields_unavailable(self):
        self.settings.bangumi.timeout = -3.99

        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
            return []

        self.client.search.side_effect = slow
        self.assertEqual((await self.data.match_vn(vn())).status, "unavailable")

    async def test_shared_fetch_survives_cancelled_caller(self):
        ready = asyncio.Event()

        async def fetch(identifier):
            await ready.wait()
            return subject(identifier)

        self.client.subject.side_effect = fetch
        first = asyncio.create_task(self.data.subject(13))
        second = asyncio.create_task(self.data.subject(13))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        ready.set()
        self.assertEqual((await second).id, 13)
        self.assertEqual((await self.data.subject(13)).id, 13)
        self.client.subject.assert_awaited_once()
        self.assertFalse(self.data._flights)

    async def test_negative_cache_and_bounded_cache(self):
        self.client.search.return_value = []
        await self.data.match_vn(vn())
        await self.data.match_vn(vn())
        self.client.search.assert_awaited_once()
        self.client.subject.side_effect = lambda identifier: subject(identifier)
        for identifier in range(1, 260):
            await self.data.subject(identifier)
        self.assertEqual(len(self.data._cache), 256)

    async def test_ranking_score_sort_and_min_votes(self):
        self.client.search.return_value = [
            subject(1, rating={"score": 8, "total": 200, "rank": 0}),
            subject(2, rating={"score": 9, "total": 100}),
            subject(3, rating={"score": 10, "total": 2}),
        ]
        items = await self.data.ranking(2024, "galgame")
        self.assertEqual([item.id for item in items], [2, 1])
        self.assertEqual(self.client.search.await_args.kwargs["sort"], "score")

    async def test_expired_cache_refetches(self):
        self.client.subject.return_value = subject()
        await self.data.subject(13)
        self.data._cache["subject:13"] = (0, subject())
        await self.data.subject(13)
        self.assertEqual(self.client.subject.await_count, 2)

    async def test_terminate_cancels_flights_and_closes_client(self):
        async def slow(identifier):
            await asyncio.sleep(10)
            return subject(identifier)

        self.client.subject.side_effect = slow
        caller = asyncio.create_task(self.data.subject(13))
        await asyncio.sleep(0)
        await self.data.terminate()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(self.data._flights)
        self.assertFalse(self.data._cache)
        self.client.terminate.assert_awaited_once()


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings()
        self.requests = []
        self.status = 200
        self.response = subject().model_dump()

        async def handler(request):
            self.requests.append(
                {
                    "path": request.path,
                    "query": dict(request.query),
                    "headers": dict(request.headers),
                    "body": await request.json() if request.method == "POST" else None,
                }
            )
            return web.json_response(
                self.response, status=self.status, headers={"Retry-After": "10"}
            )

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.api_patch = patch.object(network, "_API", f"http://127.0.0.1:{port}/v0")
        self.api_patch.start()
        self.client = network.Bangumi(self.settings)

    async def asyncTearDown(self):
        await self.client.terminate()
        self.api_patch.stop()
        await self.runner.cleanup()

    async def test_search_filter_and_identifiable_user_agent(self):
        self.response = {
            "data": [
                subject().model_dump(),
                subject(14, type=2).model_dump(),
                subject(15, nsfw=True).model_dump(),
            ]
        }
        items = await self.client.search(
            "星空", year=2024, tag="galgame", min_votes=100, sort="score"
        )
        self.assertEqual([item.id for item in items], [13])
        request = self.requests[0]
        self.assertEqual(
            request["body"]["filter"],
            {
                "type": [4],
                "nsfw": False,
                "air_date": [">=2024-01-01", "<2025-01-01"],
                "tag": ["galgame"],
                "rating_count": [">=100"],
            },
        )
        version = next(
            line.split(":", 1)[1].strip().removeprefix("v")
            for line in (ROOT / "metadata.yaml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version:")
        )
        self.assertIn(f"GalAtelier/{version}", request["headers"]["User-Agent"])
        self.assertNotIn("Authorization", request["headers"])

    async def test_token_nsfw_and_game_type(self):
        self.settings.bangumi.token = "unit-test-token-not-a-secret"
        self.settings.safety.enable_nsfw = True
        self.response = {"data": [subject(nsfw=True).model_dump()]}
        self.assertEqual(len(await self.client.search("星空")), 1)
        self.assertNotIn("nsfw", self.requests[0]["body"]["filter"])
        self.assertEqual(
            self.requests[0]["headers"]["Authorization"], "Bearer unit-test-token-not-a-secret"
        )
        self.response = subject(type=2).model_dump()
        self.assertIsNone(await self.client.subject(876))

    async def test_404_and_malformed_and_cooldown(self):
        self.status = 404
        self.assertIsNone(await self.client.subject(99))
        self.status = 200
        self.response = {"bad": "response"}
        with self.assertRaises(Tips):
            await self.client.subject(13)
        self.status = 429
        with self.assertRaises(Tips):
            await self.client.subject(13)
        with self.assertRaises(Tips):
            await self.client.subject(13)
        self.assertEqual(len(self.requests), 3)

    async def test_redirect_not_followed(self):
        self.status = 302
        with self.assertRaises(Tips):
            await self.client.subject(13)
        self.assertEqual(len(self.requests), 1)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, match):
        assets = types.SimpleNamespace(
            fetch=AsyncMock(return_value="data:image/png;base64,eA=="),
            vndb_covers=AsyncMock(
                side_effect=lambda items: ["data:image/png;base64,eA=="] * len(items)
            ),
        )
        return types.SimpleNamespace(
            settings=Settings(),
            bangumi=types.SimpleNamespace(
                match_vn=AsyncMock(return_value=match), search=AsyncMock()
            ),
            vndb=types.SimpleNamespace(
                request_by_event_vn=AsyncMock(return_value=(vn(description="VNDB fallback"), [])),
                request_by_vn=AsyncMock(return_value=[vn()]),
            ),
            assets=assets,
        )

    async def test_daily_push_scores_and_bangumi_intro(self):
        runtime = self.runtime(BangumiMatch(subject(), "精确标题", "matched"))
        daily = module("command.daily").DailyDigest(runtime)
        daily._intro = AsyncMock(side_effect=AssertionError("不应访问 TouchGal"))
        daily.render = AsyncMock(return_value="image-url")
        await daily._build_event_vn(["2026", "10", "10"], "测试日期")
        data = daily.render.await_args.args[1]
        self.assertEqual([r["source"] for r in data["ratings"]], ["Bangumi", "VNDB"])
        self.assertEqual(data["desc"], ["虚构作品的测试简介。"])
        daily._intro.assert_not_awaited()

    async def test_daily_unavailable_still_has_vndb(self):
        runtime = self.runtime(BangumiMatch(status="unavailable"))
        daily_module = module("command.daily")
        daily = daily_module.DailyDigest(runtime)
        daily._intro = AsyncMock(return_value=([daily_module._NO_INTRO], []))
        daily.render = AsyncMock(return_value="image-url")
        await daily._build_event_vn(["2026", "10", "10"], "测试日期")
        data = daily.render.await_args.args[1]
        self.assertEqual(data["ratings"][0]["source"], "VNDB")
        self.assertEqual(data["desc"], ["VNDB fallback"])
        self.assertIn("Bangumi：暂不可用", data["info"])

    async def test_keyword_bangumi_first_and_vndb_fallback(self):
        runtime = self.runtime(BangumiMatch(status="unavailable"))
        cmd = module("command.vn").VnCommand(runtime, object())
        cmd.picture = AsyncMock(return_value="result")
        cmd.bangumi_cards = AsyncMock(return_value=[])
        runtime.bangumi.search.return_value = [subject()]
        self.assertEqual([result async for result in cmd.execute("星空")], ["result"])
        runtime.vndb.request_by_vn.assert_not_awaited()
        runtime.bangumi.search.side_effect = Tips("不可用")
        self.assertEqual([result async for result in cmd.execute("星空")], ["result"])
        runtime.vndb.request_by_vn.assert_awaited_once()

    async def test_llm_tools_match_documented_inventory(self):
        llm = module("service.llm_tools")
        runtime = self.runtime(BangumiMatch())
        tools = llm.build_tools(runtime)
        self.assertEqual(tuple(tool.name for tool in tools), llm.TOOL_NAMES)
        self.assertEqual(len(tools), 4)
        runtime.bangumi.search.return_value = [subject()]
        output = await tools[0].call(None, keyword="星空")
        self.assertIn("Bangumi 评分：8.2/10", output)
        runtime.vndb.request_by_vn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
