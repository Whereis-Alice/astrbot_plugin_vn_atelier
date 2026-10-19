"""每日精选 / 清单 / 新作的离线回归，全部使用合成数据。"""

from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
Vndb = module("network.vndb").Vndb
Vn = module("types").VndbVnResponse
Character = module("types").VndbCharacterResponse
Layout = module("types").Layout
Theme = module("types").Theme
Tips = module("types").Tips
Renderer = module("service.renderer").Renderer
BangumiSubject = module("types.bangumi").BangumiSubject
BangumiCharacter = module("types.bangumi").BangumiCharacter
BangumiMatch = module("service.bangumi").BangumiMatch
BangumiData = module("service.bangumi").BangumiData
CalendarComposer = module("command.calendar").CalendarComposer
DailyDigest = module("command.daily").DailyDigest
DATE = ["2026", "10", "19"]
IMAGE = "data:image/png;base64,eA=="


def vn(identifier="v1", **changes):
    return Vn.model_validate(
        {
            "id": identifier,
            "title": "Star Road",
            "alttitle": "星の道",
            "released": "2024-10-19",
            **changes,
        }
    )


def character(identifier="c1", **changes):
    return Character.model_validate(
        {
            "id": identifier,
            "name": "Hoshi Aoi",
            "original": "星あおい",
            "birthday": [10, 19],
            "vns": [
                {"id": "v1", "title": "Star Road", "alttitle": "星の道", "released": "2024-10-19"}
            ],
            **changes,
        }
    )


def subject(identifier=10, **changes):
    return BangumiSubject.model_validate(
        {
            "id": identifier,
            "type": 4,
            "name": "星の道",
            "name_cn": "星之路",
            "date": "2024-10-19",
            "summary": "中文作品简介。",
            "rating": {"score": 8.2, "total": 200},
            **changes,
        }
    )


def runtime():
    return types.SimpleNamespace(
        settings=Settings(),
        vndb=types.SimpleNamespace(
            request_anniversaries=AsyncMock(return_value=[vn()]),
            request_birthdays=AsyncMock(return_value=[character()]),
            request_new_releases=AsyncMock(return_value=[vn(released="2026-10-19")]),
            request_by_event_cha=AsyncMock(return_value=([character()], [vn()])),
        ),
        bangumi=types.SimpleNamespace(
            match_vn=AsyncMock(return_value=BangumiMatch(subject(), "精确标题", "matched")),
            match_character=AsyncMock(return_value=None),
        ),
        assets=types.SimpleNamespace(fetch=AsyncMock(return_value=IMAGE)),
    )


class SettingsTests(unittest.TestCase):
    def test_defaults_keep_featured_and_opt_in_new_columns(self):
        settings = Settings.from_config({}).schedule
        self.assertTrue(settings.featured)
        self.assertFalse(settings.calendar)
        self.assertFalse(settings.new_releases)
        self.assertEqual(
            (settings.anniversary_count, settings.birthday_count, settings.new_release_count),
            (12, 12, 12),
        )

    def test_schema_and_runtime_agree_and_bad_values_clamp(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        defaults = {
            key: value["default"] for key, value in schema["scheduleSetting"]["items"].items()
        }
        self.assertEqual(
            Settings.from_config({"scheduleSetting": defaults}).schedule,
            Settings.from_config({}).schedule,
        )
        schedule = Settings.from_config(
            {
                "scheduleSetting": {
                    "enableFeatured": "false",
                    "enableCalendar": "true",
                    "enableNewReleases": "yes",
                    "anniversaryCount": 0,
                    "birthdayCount": 999,
                    "newReleaseCount": "oops",
                }
            }
        ).schedule
        self.assertFalse(schedule.featured)
        self.assertTrue(schedule.calendar and schedule.new_releases)
        self.assertEqual(
            (schedule.anniversary_count, schedule.birthday_count, schedule.new_release_count),
            (1, 50, 12),
        )


class QueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings()
        self.http = types.SimpleNamespace(post=AsyncMock(return_value={"results": []}))
        self.client = Vndb(self.http, self.settings)

    def payload(self):
        return self.http.post.await_args.args[1]

    async def test_anniversaries_exclude_current_year_and_no_rating_gate(self):
        await self.client.request_anniversaries(DATE, limit=24)
        payload = self.payload()
        self.assertEqual(payload["results"], 24)
        text = json.dumps(payload)
        self.assertIn("2025-10-19", text)
        self.assertNotIn("2026-10-19", text)
        self.assertNotIn('["rating", ">="', text)
        self.assertNotIn('"count"', text)

    async def test_leap_day_only_valid_years(self):
        filters = self.client._released_filters(["2026", "02", "29"])
        self.assertIn(["released", "=", "2024-02-29"], filters)
        self.assertNotIn(["released", "=", "2025-02-29"], filters)
        self.settings.event.start_year = 2026
        self.assertEqual(await self.client.request_anniversaries(DATE), [])
        self.http.post.assert_not_awaited()

    async def test_birthdays_exclude_before_limit_and_include_origins(self):
        await self.client.request_birthdays(DATE, limit=18, exclude=["c1", "c2", "c1"])
        payload = self.payload()
        self.assertEqual(payload["results"], 18)
        self.assertEqual(
            payload["filters"],
            ["and", ["birthday", "=", [10, 19]], ["id", "!=", "c1"], ["id", "!=", "c2"]],
        )
        self.assertIn("vns{", payload["fields"])
        self.assertNotIn("role", json.dumps(payload["filters"]))
        self.assertNotIn("rating", json.dumps(payload["filters"]))

    async def test_new_releases_are_exact_vn_date_no_scores(self):
        await self.client.request_new_releases(DATE, limit=17)
        self.assertTrue(self.http.post.await_args.args[0].endswith("/vn"))
        payload = self.payload()
        self.assertEqual(payload["filters"], ["released", "=", "2026-10-19"])
        self.assertEqual(payload["results"], 17)
        self.assertIn("description", payload["fields"])
        self.assertNotIn("rating", json.dumps(payload["filters"]))

    async def test_same_work_siblings_ignore_initial_role_and_gender_and_page(self):
        self.settings.schedule.gender_filter = "a"
        first = character(sex=["f", None]).model_dump()
        sibling = character("c2", original="星そら", sex=["m", None]).model_dump()
        self.http.post.side_effect = [
            {"results": [first]},
            {"results": [{"id": "v1"}]},
            {"results": [first], "more": True},
            {"results": [sibling], "more": False},
            {"results": [vn().model_dump()]},
        ]
        characters, works = await self.client.request_by_event_cha(DATE)
        self.assertEqual([item.id for item in characters], ["c1", "c2"])
        self.assertEqual([item.id for item in works], ["v1"])
        for call in self.http.post.await_args_list[2:4]:
            filters = call.args[1]["filters"]
            self.assertEqual(
                filters, ["and", ["birthday", "=", [10, 19]], ["vn", "=", ["id", "=", "v1"]]]
            )
        self.assertEqual(self.http.post.await_args_list[3].args[1]["page"], 2)

    async def test_no_featured_candidate_is_handled(self):
        with self.assertRaises(Tips):
            await self.client.request_by_event_cha(DATE)


class CharacterMatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = types.SimpleNamespace(
            subject_characters=AsyncMock(return_value=[BangumiCharacter(id=20, name="星あおい")]),
            character=AsyncMock(
                return_value=BangumiCharacter(
                    id=20,
                    name="星あおい",
                    birth_mon=10,
                    birth_day=19,
                    infobox=[{"key": "简体中文名", "value": "星葵"}],
                )
            ),
            terminate=AsyncMock(),
        )
        self.data = BangumiData(self.client, Settings())

    async def asyncTearDown(self):
        await self.data.terminate()

    async def test_exact_name_in_confirmed_game_localizes_and_caches(self):
        result = await self.data.match_character(character(), [subject()])
        self.assertEqual(result.display_name, "星葵")
        self.assertEqual((await self.data.match_character(character(), [subject()])).id, 20)
        self.client.subject_characters.assert_awaited_once_with(10)
        self.client.character.assert_awaited_once_with(20)

    async def test_no_confirmed_subject_no_character_lookup(self):
        self.assertIsNone(await self.data.match_character(character(), []))
        self.client.subject_characters.assert_not_awaited()

    async def test_ambiguous_same_name_is_rejected(self):
        self.client.subject_characters.return_value.append(BangumiCharacter(id=21, name="星あおい"))
        self.assertIsNone(await self.data.match_character(character(), [subject()]))
        self.client.character.assert_not_awaited()

    async def test_conflicting_birthday_or_changed_full_name_rejected(self):
        for detail in (
            BangumiCharacter(id=20, name="星あおい", birth_mon=1),
            BangumiCharacter(id=20, name="別人"),
        ):
            with self.subTest(detail=detail):
                self.data._cache.clear()
                self.client.character.return_value = detail
                self.assertIsNone(await self.data.match_character(character(), [subject()]))

    async def test_unavailable_character_data_retains_original(self):
        self.client.subject_characters.side_effect = Tips("暂不可用")
        self.assertIsNone(await self.data.match_character(character(), [subject()]))

    async def test_disabled_enrichment_does_not_access_character_api(self):
        self.data.settings.bangumi.enrich = False
        self.assertIsNone(await self.data.match_character(character(), [subject()]))
        self.client.subject_characters.assert_not_awaited()


class CompositionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = runtime()
        self.composer = CalendarComposer(self.runtime)

    def test_vndb_pictures_require_clear_flag_unless_nsfw_enabled(self):
        for flag in (None, 0.5, 2, float("nan")):
            self.assertEqual(
                self.composer.vndb_picture(
                    vn(image={"url": "https://example.invalid/cover.jpg", "sexual": flag})
                ),
                "",
            )
        safe = vn(image={"url": "https://example.invalid/cover.jpg", "sexual": 0})
        self.assertTrue(self.composer.vndb_picture(safe).endswith("cover.jpg"))
        self.runtime.settings.safety.enable_nsfw = True
        self.assertTrue(
            self.composer.vndb_picture(
                vn(image={"url": "https://example.invalid/cover.jpg", "sexual": 2})
            )
        )

    async def test_calendar_two_sections_character_has_origins_not_work_ratings(self):
        data = await self.composer.calendar_data(DATE, "测试日期", exclude=["c7"])
        self.assertEqual([s["kind"] for s in data["sections"]], ["anniversaries", "birthdays"])
        work, char = [s["cards"][0] for s in data["sections"]]
        self.assertEqual(work["subtitle"], "星之路")
        self.assertEqual(work["badge"], "发布 2 周年")
        self.assertEqual(work["ratings"][0]["source"], "Bangumi")
        self.assertIn("来源作品：星之路（v1）", char["desc"])
        self.assertNotIn("ratings", char)
        self.assertFalse(any("简介" in line or "评分" in line for line in char["desc"]))
        self.runtime.vndb.request_birthdays.assert_awaited_once_with(DATE, limit=12, exclude=["c7"])

    async def test_unmatched_characters_keep_original_name_and_source(self):
        self.runtime.bangumi.match_vn.return_value = BangumiMatch(status="ambiguous")
        result = (await self.composer.birthday_cards([character()]))[0]
        self.assertEqual(result["subtitle"], "星あおい")
        self.assertIn("来源作品：星の道（v1）", result["desc"])
        self.assertEqual(self.runtime.bangumi.match_character.await_args.args[1], [])

    async def test_localized_character_retains_original(self):
        self.runtime.bangumi.match_character.return_value = BangumiCharacter(
            id=20, name="星あおい", infobox=[{"key": "简体中文名", "value": "星葵"}]
        )
        item = (await self.composer.birthday_cards([character()]))[0]
        self.assertEqual(item["subtitle"], "星葵")
        self.assertIn("原名：星あおい", item["desc"])

    async def test_missing_origins_and_images_remain_visible(self):
        item = (await self.composer.birthday_cards([character(vns=None)]))[0]
        self.assertIn("来源作品：暂无关联作品", item["desc"])
        self.assertEqual(item["image"], IMAGE)

    async def test_one_calendar_query_fails_other_section_survives(self):
        self.runtime.vndb.request_anniversaries.side_effect = Tips("失败")
        data = await self.composer.calendar_data(DATE, "测试")
        self.assertEqual(len(data["sections"]), 1)
        self.assertEqual(data["sections"][0]["kind"], "birthdays")
        self.assertTrue(any("暂未能查询" in line for line in data["notes"]))

    async def test_no_calendar_results_no_empty_image(self):
        self.runtime.vndb.request_anniversaries.return_value = []
        self.runtime.vndb.request_birthdays.return_value = []
        self.assertIsNone(await self.composer.calendar_data(DATE, "测试"))

    async def test_new_release_prefers_bgm_intro_without_replacing_source_date(self):
        data = await self.composer.new_release_data(DATE, "测试")
        item = data["sections"][0]["cards"][0]
        self.assertEqual(item["subtitle"], "星之路")
        self.assertIn("中文作品简介。", item["desc"])
        self.assertIn("VNDB 首发：2026-10-19", item["desc"])
        self.assertTrue(any("发行计划" in line for line in data["notes"]))

    async def test_new_unrated_vndb_fallback_and_long_intro_clipped(self):
        self.runtime.vndb.request_new_releases.return_value = [
            vn(description="[spoiler]秘密[/spoiler]" + "长" * 700)
        ]
        self.runtime.bangumi.match_vn.return_value = BangumiMatch(status="unavailable")
        item = (await self.composer.new_release_data(DATE, "测试"))["sections"][0]["cards"][0]
        self.assertEqual(item["subtitle"], "星の道")
        self.assertEqual(item["ratings"][0]["score"], "暂无")
        self.assertTrue(any(len(line) == 600 for line in item["desc"]))
        self.assertFalse(any("秘密" in line for line in item["desc"]))

    async def test_new_empty_results_do_not_generate_image(self):
        self.runtime.vndb.request_new_releases.return_value = []
        self.assertIsNone(await self.composer.new_release_data(DATE, "测试"))

    async def test_templates_four_themes_keep_all_rows_and_escape_input(self):
        self.runtime.vndb.request_birthdays.return_value = [
            character(f"c{i}", original=f"寿星{i}<script>x</script>") for i in range(1, 13)
        ]
        self.runtime.vndb.request_anniversaries.return_value = [vn(f"v{i}") for i in range(1, 13)]
        data = await self.composer.calendar_data(DATE, "测试")
        for theme in Theme:
            with self.subTest(theme=theme):
                self.runtime.settings.render.theme = theme
                renderer = Renderer(self.runtime.settings, ROOT / "resources")
                payload = renderer.prepare(data)
                payload.update(renderer._common(), head_sub=data["subtitle"])
                html = (
                    Environment(autoescape=True)
                    .from_string(renderer.template(Layout.DOSSIER))
                    .render(payload)
                )
                self.assertEqual(html.count('<article class="card">'), 24)
                self.assertIn("sec--birthdays", html)
                self.assertIn("寿星12&lt;script&gt;", html)
                self.assertNotIn("<script>", html)
                self.assertNotIn("line-clamp", html)


class DigestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = runtime()
        self.daily = DailyDigest(self.runtime)

    async def test_defaults_only_featured_two_separate_images(self):
        for name in (
            "_build_event_vn",
            "_build_event_cha",
            "_build_calendar",
            "_build_new_releases",
        ):
            setattr(self.daily, name, AsyncMock(return_value=name))
        self.assertEqual(await self.daily.build(), ["_build_event_vn", "_build_event_cha"])
        self.daily._build_calendar.assert_not_awaited()
        self.daily._build_new_releases.assert_not_awaited()

    async def test_all_columns_separate_images_in_order_and_fail_independently(self):
        self.runtime.settings.schedule.calendar = True
        self.runtime.settings.schedule.new_releases = True
        self.daily._build_event_vn = AsyncMock(side_effect=Tips("精选失败"))
        self.daily._build_event_cha = AsyncMock(return_value="birthday-feature")
        self.daily._build_calendar = AsyncMock(return_value="calendar-image")
        self.daily._build_new_releases = AsyncMock(return_value="new-image")
        self.assertEqual(
            await self.daily.build(), ["birthday-feature", "calendar-image", "new-image"]
        )

    async def test_featured_off_no_featured_queries(self):
        self.runtime.settings.schedule.featured = False
        self.runtime.settings.schedule.calendar = True
        self.daily._build_event_vn = AsyncMock()
        self.daily._build_event_cha = AsyncMock()
        self.daily._build_calendar = AsyncMock(return_value="calendar-only")
        self.assertEqual(await self.daily.build(), ["calendar-only"])
        self.daily._build_event_vn.assert_not_awaited()
        self.daily._build_event_cha.assert_not_awaited()

    async def test_successful_sibling_card_dedupes_both_from_calendar(self):
        self.runtime.vndb.request_by_event_cha.return_value = (
            [character(), character("c2")],
            [vn()],
        )
        self.daily.render = AsyncMock(return_value="sibling-image")
        self.daily.vn_cards = AsyncMock(return_value=[])
        await self.daily._build_event_cha(DATE, "测试")
        self.assertEqual(self.daily.render.await_args.args[0], Layout.DOSSIER)
        self.assertEqual(len(self.daily.render.await_args.args[1]["sections"][0]["cards"]), 2)
        self.assertEqual(self.daily._birthday_ids, {"c1", "c2"})
        self.daily.calendar_data = AsyncMock(return_value=None)
        await self.daily._build_calendar(DATE, "测试")
        self.assertEqual(self.daily.calendar_data.await_args.kwargs["exclude"], ["c1", "c2"])

    async def test_single_feature_stays_feature_layout_and_failure_not_excluded(self):
        self.daily._birthday_ids = set()
        self.daily.vn_cards = AsyncMock(return_value=[])
        self.daily.render = AsyncMock(side_effect=Tips("图片失败"))
        with self.assertRaises(Tips):
            await self.daily._build_event_cha(DATE, "测试")
        self.assertEqual(self.daily._birthday_ids, set())
        self.assertEqual(self.daily.render.await_args.args[0], Layout.FEATURE)

    async def test_empty_optional_columns_are_skipped(self):
        self.daily._birthday_ids = set()
        self.daily.calendar_data = AsyncMock(return_value=None)
        self.daily.new_release_data = AsyncMock(return_value=None)
        self.daily.render = AsyncMock()
        self.assertEqual(await self.daily._build_calendar(DATE, "测试"), "")
        self.assertEqual(await self.daily._build_new_releases(DATE, "测试"), "")
        self.daily.render.assert_not_awaited()

    async def test_scheduled_transport_sends_four_independent_messages(self):
        entry = importlib.import_module(PKG + ".main")
        plugin = object.__new__(entry.VnAtelierPlugin)
        plugin.runtime = self.runtime
        self.runtime.subscription = types.SimpleNamespace(
            targets=AsyncMock(return_value=["platform:GroupMessage:1"])
        )
        plugin.context = types.SimpleNamespace(send_message=AsyncMock(return_value=True))
        digest = types.SimpleNamespace(
            build=AsyncMock(
                return_value=[
                    f"https://example.invalid/{name}.jpg"
                    for name in ("featured-vn", "featured-birthday", "calendar", "new-releases")
                ]
            )
        )
        with patch.object(entry, "DailyDigest", return_value=digest):
            await plugin._push_today()
        self.assertEqual(plugin.context.send_message.await_count, 4)
        for call in plugin.context.send_message.await_args_list:
            self.assertEqual(call.args[0], "platform:GroupMessage:1")
            self.assertEqual(len(call.args[1].chain), 1)

    async def test_failed_send_does_not_cancel_other_columns(self):
        entry = importlib.import_module(PKG + ".main")
        plugin = object.__new__(entry.VnAtelierPlugin)
        plugin.runtime = self.runtime
        self.runtime.subscription = types.SimpleNamespace(
            targets=AsyncMock(return_value=["platform:GroupMessage:1"])
        )
        plugin.context = types.SimpleNamespace(
            send_message=AsyncMock(side_effect=[RuntimeError("单图发送失败"), False, True, True])
        )
        digest = types.SimpleNamespace(
            build=AsyncMock(return_value=[f"https://example.invalid/{i}.jpg" for i in range(4)])
        )
        with patch.object(entry, "DailyDigest", return_value=digest):
            await plugin._push_today()
        self.assertEqual(plugin.context.send_message.await_count, 4)

    async def test_manual_calendar_does_not_apply_scheduled_exclusions(self):
        cmd = module("command.event").EventCommand(self.runtime, object())
        cmd.picture = AsyncMock(return_value="manual-image")
        results = [result async for result in cmd.execute("10-19")]
        self.assertEqual(results, ["manual-image"])
        self.assertEqual(self.runtime.vndb.request_birthdays.await_args.kwargs["exclude"], ())

    async def test_manual_new_release_empty_returns_clear_text(self):
        self.runtime.vndb.request_new_releases.return_value = []
        event = types.SimpleNamespace(plain_result=lambda text: text)
        cmd = module("command.new_releases").NewReleasesCommand(self.runtime, event)
        result = [item async for item in cmd.execute("")]
        self.assertIn("不代表今天一定没有新作", result[0])


class CharacterNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_related_character_parser_skips_bad_and_nonperson_entries(self):
        client = module("network.bangumi").Bangumi(Settings())
        client._request = AsyncMock(
            return_value=[
                {"id": 1, "name": "角色", "type": 1, "images": None},
                {"id": 2, "type": 2},
                {"bad": "value"},
            ]
        )
        self.assertEqual([item.id for item in await client.subject_characters(10)], [1])
        client._request.assert_awaited_once_with("/subjects/10/characters")

    async def test_detail_missing_and_malformed_response(self):
        client = module("network.bangumi").Bangumi(Settings())
        client._request = AsyncMock(return_value=None)
        self.assertIsNone(await client.character(20))
        client._request.return_value = {"bad": "response"}
        with self.assertRaises(Tips):
            await client.character(20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
