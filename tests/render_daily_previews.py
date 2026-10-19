"""可选视觉自检：使用合成数据与代码绘图，需本机 Playwright Chromium。

运行 python tests/render_daily_previews.py，不访问第三方站点，不连接 AstrBot。
输出 docs/preview/ 中的清单、新作、同作寿星示例，并检查 DOM 是否横向溢出。
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
from unittest.mock import AsyncMock

from jinja2 import Environment
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright
from test_daily import (
    DATE,
    ROOT,
    BangumiMatch,
    CalendarComposer,
    DailyDigest,
    Layout,
    Renderer,
    Theme,
    character,
    runtime,
    subject,
    vn,
)


def artwork(portrait: bool = False) -> str:
    """程序绘制的星空封面或极简角色头像，不使用第三方图片。"""
    image = Image.new("RGB", (300, 420), "#142238")
    draw = ImageDraw.Draw(image)
    for y in range(420):
        draw.line((0, y, 300, y), fill=(20 + y // 16, 34 + y // 13, 56 + y // 10))
    for i in range(60):
        x, y = (i * 137) % 300, (i * 83) % 300
        draw.ellipse((x, y, x + 2, y + 2), fill="#d0e5ef")
    draw.ellipse((176, 35, 251, 110), fill="#e0d2ac")
    if portrait:
        draw.polygon([(63, 420), (86, 259), (214, 259), (237, 420)], fill="#b8c3db")
        draw.ellipse((70, 96, 230, 301), fill="#313847")
        draw.ellipse((96, 134, 204, 269), fill="#f3d4c3")
        draw.polygon(
            [(87, 173), (107, 112), (200, 127), (214, 174), (171, 148), (144, 177), (132, 151)],
            fill="#313847",
        )
        for x in (125, 171):
            draw.ellipse((x, 193, x + 8, 202), fill="#4e688e")
        draw.arc((139, 224, 162, 238), 10, 170, fill="#ad7f7a", width=2)
    else:
        draw.polygon(
            [(0, 335), (80, 237), (167, 342), (244, 287), (300, 364), (300, 420), (0, 420)],
            fill="#273d55",
        )
        for i in range(12):
            y = 328 + i * 7
            draw.line((150 - i * 5, y, 153 + i * 6, y), fill="#b3b9ba", width=2)
        for i in range(7):
            a = i * math.pi / 3
            draw.line(
                (62, 122, 62 + int(28 * math.cos(a)), 122 + int(28 * math.sin(a))),
                fill="#9cb8d0",
                width=2,
            )
    output = io.BytesIO()
    image.save(output, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()


async def build_samples():
    rt = runtime()
    rt.settings.render.font_source = "system"
    rt.settings.render.font_family = "Microsoft YaHei"
    works = [
        vn(
            f"v{i}",
            title=f"Star Road {i}",
            alttitle=f"星空旅路 · 第{i}章",
            released=f"{2000 + i}-10-19",
            rating=86 - i,
            votecount=500 + i * 53,
        )
        for i in range(1, 13)
    ]
    names = [
        "星野葵",
        "月见澪",
        "白羽诗音",
        "七濑灯里",
        "朝雾遥",
        "雨宫凛",
        "风间千夏",
        "樱庭奏",
        "青叶萤",
        "雪村悠",
        "水原茉莉",
        "小鸟游律",
    ]
    characters = [
        character(
            f"c{i}",
            original=name,
            aliases=[f"寿星 {i}"],
            sex=["f", None],
            vns=[works[(i - 1) // 2].model_dump()],
            image={"url": "https://example.invalid/character.jpg", "sexual": 0},
        )
        for i, name in enumerate(names, 1)
    ]

    async def match(work):
        index = int(work.id[1:])
        if index % 5 == 0:
            return BangumiMatch(status="ambiguous")
        return BangumiMatch(
            subject(
                index,
                name=work.display_title,
                name_cn=work.display_title,
                date=work.released,
                rating={"score": 8.2 - index / 20, "total": 200 + index * 37},
                infobox=[{"key": "开发", "value": "星空制作室"}, {"key": "平台", "value": "PC"}],
                images={"large": "https://example.invalid/cover.jpg"},
            ),
            "精确标题",
            "matched",
        )

    cover, portrait = artwork(), artwork(True)

    async def fetch(group, url, **kwargs):
        return portrait if "character" in url else cover

    rt.assets.fetch.side_effect = fetch
    rt.bangumi.match_vn.side_effect = match
    rt.vndb.request_anniversaries.return_value = works
    rt.vndb.request_birthdays.return_value = characters
    composer = CalendarComposer(rt)
    calendar = await composer.calendar_data(DATE, "10 月 19 日")
    rt.vndb.request_new_releases.return_value = [
        vn(
            "v1",
            alttitle="风与归途",
            released="2026-10-19",
            description="从小镇启程，在风车与星光之间，寻找许久未见的约定。",
        ),
        vn(
            "v5",
            alttitle="雨后的第七封信",
            released="2026-10-19",
            description="未能可靠匹配 Bangumi 的新作，继续展示原始资料。",
            rating=None,
        ),
        vn("v3", alttitle="夜航的纸飞机", released="2026-10-19"),
    ]
    releases = await composer.new_release_data(DATE, "10 月 19 日")
    rt.vndb.request_by_event_cha.return_value = (characters[:2], works[:1])
    daily = DailyDigest(rt)
    captured = []

    async def capture(layout, data):
        captured.append(data)
        return "https://example.invalid/sibling.jpg"

    daily.render = AsyncMock(side_effect=capture)
    await daily._build_event_cha(DATE, "10 月 19 日")
    for data in (calendar, releases, captured[0]):
        data["subtitle"] = "虚构数据与程序绘图 · 仅供版式预览"
    return rt, [
        ("daily_calendar", calendar),
        ("daily_new_releases", releases),
        ("daily_same_work_birthdays", captured[0]),
    ]


async def main():
    rt, samples = await build_samples()
    output = ROOT / "docs" / "preview"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1000, "height": 1000}, device_scale_factor=1.3
        )
        for name, data in samples:
            for theme in Theme:
                rt.settings.render.theme = theme
                renderer = Renderer(rt.settings, ROOT / "resources")
                payload = renderer.prepare(data)
                payload.update(renderer._common(), head_sub=renderer.prepare(data["subtitle"]))
                html = (
                    Environment(autoescape=True)
                    .from_string(renderer.template(Layout.DOSSIER))
                    .render(payload)
                )
                await page.set_content(html, wait_until="load")
                await page.evaluate("document.fonts.ready")
                overflow = await page.evaluate(
                    """() => [...document.querySelectorAll('.card, .card__body, .sec')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.className)"""
                )
                if overflow:
                    raise RuntimeError(f"{name}/{theme}: 横向溢出 {overflow}")
                if theme is Theme.STARLIT:
                    target = output / (name + ".jpg")
                    await page.screenshot(
                        path=str(target), full_page=True, type="jpeg", quality=92, scale="device"
                    )
                    print(target, await page.locator("body").bounding_box())
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
