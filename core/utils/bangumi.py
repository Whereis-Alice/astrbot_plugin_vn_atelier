"""供卡片和文本工具共用的 Bangumi 数据格式。"""

from __future__ import annotations

import math
from typing import Any

from ..service.bangumi import BangumiMatch
from ..types import VndbVnResponse
from ..types.bangumi import BangumiSubject


def bangumi_lines(item: BangumiSubject, *, compact: bool = False) -> list[str]:
    value = item.rating.value
    lines = [f"Bangumi ID：{item.id}"]
    lines.append(
        f"Bangumi 评分：{value:.1f}/10 · {item.rating.total:,} 人评价"
        if value is not None
        else "Bangumi 评分：暂无评分"
    )
    if item.rating.rank > 0:
        lines.append(f"Bangumi 游戏排名：#{item.rating.rank:,}")
    if item.date:
        lines.append(f"Bangumi 发售日：{item.date}")
    if item.name_cn and item.name != item.name_cn:
        lines.append(f"原名：{item.name}")
    for keys, label in [(("平台",), "平台"), (("开发", "开发商"), "开发"), (("游戏类型",), "类型")]:
        values = item.info(*keys)
        if values:
            lines.append(f"{label}：{'、'.join(values)[:240]}")
    if not compact:
        for keys, label in [
            (("发行", "发行商"), "发行"),
            (("剧本",), "剧本"),
            (("原画",), "原画"),
            (("音乐",), "音乐"),
        ]:
            values = item.info(*keys)
            if values:
                lines.append(f"{label}：{'、'.join(values)[:240]}")
        names = [str(tag.get("name", "")) for tag in item.tags[:8] if tag.get("name")]
        if names:
            lines.append(f"标签：{'、'.join(names)}")
        collection = item.collection
        if collection:
            lines.append(
                "收藏："
                + " · ".join(
                    f"{label} {collection.get(key, 0):,}"
                    for key, label in [
                        ("wish", "想玩"),
                        ("doing", "在玩"),
                        ("collect", "玩过"),
                        ("on_hold", "搁置"),
                        ("dropped", "抛弃"),
                    ]
                )
            )
    lines.append(item.url)
    return lines


def match_lines(match: BangumiMatch) -> list[str]:
    if match.subject is not None:
        return [*bangumi_lines(match.subject, compact=True), f"跨站关联：{match.method}"]
    labels = {
        "unmatched": "未找到可靠关联条目",
        "ambiguous": "候选有歧义，未关联评分",
        "unavailable": "暂不可用",
    }
    return [f"Bangumi：{labels[match.status]}"] if match.status in labels else []


def rating_data(
    item: BangumiSubject | None = None, vn: VndbVnResponse | None = None
) -> list[dict[str, Any]]:
    ratings = []
    if item is not None:
        ratings.append(
            {
                "source": "Bangumi",
                "score": f"{item.rating.value:.1f}" if item.rating.value is not None else "暂无",
                "scale": "/10" if item.rating.value is not None else "",
                "meta": f"{item.rating.total:,} 人评价"
                if item.rating.value is not None
                else "尚无有效评分",
                "rank": f"游戏 #{item.rating.rank:,}" if item.rating.rank > 0 else "未排名",
            }
        )
    if vn is not None and vn.rating is not None:
        valid = math.isfinite(vn.rating) and 0 < vn.rating <= 100 and vn.votecount != 0
        ratings.append(
            {
                "source": "VNDB",
                "score": f"{vn.rating:.1f}" if valid else "暂无",
                "scale": "/100" if valid else "",
                "meta": (f"{vn.votecount:,} 人评价" if vn.votecount is not None else "贝叶斯评分")
                if valid
                else "尚无有效评分",
                "rank": "贝叶斯评分" if valid else "未评分",
            }
        )
    return ratings


def distribution_data(item: BangumiSubject) -> list[dict[str, int]]:
    counts = item.rating.count
    maximum = max(counts.values(), default=0)
    return [
        {
            "score": i,
            "count": counts.get(str(i), 0),
            "height": round(90 * counts.get(str(i), 0) / maximum) if maximum else 0,
        }
        for i in range(1, 11)
    ]
