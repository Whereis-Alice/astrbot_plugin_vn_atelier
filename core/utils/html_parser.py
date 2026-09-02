"""TouchGal 详情页解析。

站点是 Next.js 渲染的静态 HTML，没有公开的详情 API，只能按 class 名
抓取。任何 class 变动都会让解析失败，因此统一收敛成 SettingException
提示用户检查登录态，而不是抛出 AttributeError。
"""

from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..types import SettingException, TouchGalDetails

# 详情页外链图标的 SVG path，用来判断这一栏是不是「外部链接」。
_EXTERNAL_LINK_PATHS = (
    "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71",
    "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71",
)
_META_GRID_CLASS = "grid gap-4 mt-6 sm:grid-cols-2"
_TITLE_CLASS = "text-2xl font-bold leading-tight sm:text-3xl"
_PROSE_CLASS = "kun-prose max-w-none"
_GALLERY_CLASS = "data-kun-img-container"


def parse_touchgal_details(html: str) -> TouchGalDetails:
    """从详情页 HTML 里取出外链、标题、简介与预览图。"""
    soup = BeautifulSoup(html, "html.parser")
    try:
        grid = soup.find("div", class_=_META_GRID_CLASS)
        last_cell = grid.find_all("div")[-1]  # type: ignore[union-attr]
        icon_paths = last_cell.find("svg").find_all("path")
        is_external = len(icon_paths) >= 2 and all(
            icon_paths[index].get("d") == expected
            for index, expected in enumerate(_EXTERNAL_LINK_PATHS)
        )
        third_info = last_cell.find("span").get_text().split(": ") if is_external else []

        # 页面标题一律取出：随机 / 推荐这类没有 VNDB ID 外链的作品要靠它
        # 反查 VNDB，上游只在「外链不是 VNDB ID」时才解析，导致带 ID 的
        # 作品拿不到标题，后续兜底搜索无从下手。
        heading = soup.find("h1", class_=_TITLE_CLASS)
        title = heading.get_text(strip=True) if heading else ""

        prose = soup.find("div", class_=_PROSE_CLASS)
        description = "\n".join(
            node.get_text(strip=True)
            for node in prose.find_all("p", recursive=False)  # type: ignore[union-attr]
        )

        previews: list[str] = []
        gallery = prose.find("div", class_=_GALLERY_CLASS)  # type: ignore[union-attr]
        if isinstance(gallery, Tag):
            previews = [str(node["src"]) for node in gallery.find_all("img") if node.get("src")]
    except (AttributeError, IndexError, KeyError) as error:
        raise SettingException("TouchGal登录账号Token") from error

    return TouchGalDetails(
        third_info=[part.strip() for part in third_info if part.strip()],
        previews=previews,
        description=description,
        title=title,
    )


async def parse_touchgal_details_async(html: str) -> TouchGalDetails:
    """parse_touchgal_details 的线程池版本，避免大页面阻塞事件循环。"""
    return await asyncio.to_thread(parse_touchgal_details, html)
