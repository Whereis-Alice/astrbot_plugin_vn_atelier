"""HTML 渲染。

AstrBot 的 t2i 端点只接受「一整段模板字符串 + 一份数据」，Jinja 的
{% include %} 在这里用不了，所以模板按三段拼装：

    base.html（骨架 + 公共 CSS）
      └─ <!--__LAYOUT_BODY__-->  →  layout_*.html（某个布局的正文）
      └─ /*__THEME_CSS__*/       →  theme/*.css（一套配色）

拼装用的是 Python 的 str.replace 而不是 Jinja，因此模板里的占位符刻意
写成注释形态，即使某次替换漏了也不会被 Jinja 当成未定义变量。

安全性方面：t2i 端点的 Jinja 环境没有开 autoescape，所以**转义必须在
插件侧完成**。prepare() 会递归转义数据里的每一个字符串，模板统一用
{{ x | safe }} 取值——数据已经是转义后的结果，无论对端 autoescape 是
开还是关，渲染结果都一致且安全。
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import html_renderer, logger

from ..settings import Settings
from ..types import THEME_LABELS, Layout, RenderException, Theme
from ..utils import FileIO

_LOG = "[视觉小说工房]"

_BRAND = "VISUAL NOVEL ATELIER"

_THEME_MARK = "/*__THEME_CSS__*/"
_BODY_MARK = "<!--__LAYOUT_BODY__-->"

_FALLBACK_STACK = (
    '"Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei",'
    '"PingFang SC","Hiragino Sans GB",sans-serif'
)

_UNSAFE_FONT_CHARS = re.compile(r'["\'<>;{}\n\r]')


class Renderer:
    """把结构化数据渲染成一张图片的地址。"""

    def __init__(self, settings: Settings, resources: Path) -> None:
        self._settings = settings
        self._resources = resources
        self._cache: dict[tuple[Layout, Theme], str] = {}
        self._base: str | None = None
        self._layouts: dict[Layout, str] = {}
        self._themes: dict[Theme, str] = {}
        self._degraded = False
        """对端拒收过清晰度参数时置位，后续直接用兼容参数，不再白跑一次。"""

    # -- 模板装配 ---------------------------------------------------------- #

    def _read(self, *parts: str) -> str:
        return FileIO.read_text_sync(self._resources.joinpath(*parts))

    def _base_template(self) -> str:
        if self._base is None:
            self._base = self._read("template", "base.html")
        return self._base

    def _layout_body(self, layout: Layout) -> str:
        if layout not in self._layouts:
            self._layouts[layout] = self._read("template", f"layout_{layout.value}.html")
        return self._layouts[layout]

    def _theme_css(self, theme: Theme) -> str:
        if theme not in self._themes:
            self._themes[theme] = self._read("theme", f"{theme.value}.css")
        return self._themes[theme]

    def template(self, layout: Layout) -> str:
        """取（布局，主题）对应的完整模板，结果会缓存。"""
        theme = self._settings.render.theme
        key = (layout, theme)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        assembled = (
            self._base_template()
            .replace(_THEME_MARK, self._theme_css(theme))
            .replace(_BODY_MARK, self._layout_body(layout))
        )
        self._cache[key] = assembled
        return assembled

    def invalidate(self) -> None:
        """清空模板缓存。切换主题或热更新资源文件后调用。"""
        self._degraded = False
        self._cache.clear()
        self._base = None
        self._layouts.clear()
        self._themes.clear()

    # -- 数据预处理 -------------------------------------------------------- #

    #: 超过这个长度的字符串不参与 <br> 拼接——base64 图片正好长这样。
    _MAX_JOIN_LEN = 4096

    @classmethod
    def _joinable(cls, items: list[Any]) -> bool:
        """判断一个列表是否值得额外产出 <key>_html。

        只有「元素全是短字符串」的列表才需要。previews 那种 base64 图片
        列表如果也拼一份副本，请求体会凭空翻倍，端点大概率直接拒收。
        """
        return all(
            isinstance(entry, str)
            and len(entry) <= cls._MAX_JOIN_LEN
            and not entry.startswith("data:")
            for entry in items
        )

    def prepare(self, value: Any) -> Any:
        """递归转义数据中的所有字符串。

        对于「元素全是短字符串的列表」还会额外产出一个 <key>_html 字段，
        内容是用 <br> 连接好的转义文本，模板直接一次输出即可，无需在
        Jinja 里做 join——上游正是靠模板里的 join 拼 HTML，一旦对端开启
        autoescape 就会把标签原样打印出来。
        """
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                result[str(key)] = self.prepare(item)
                if isinstance(item, list) and self._joinable(item):
                    result[f"{key}_html"] = "<br>".join(
                        html.escape(entry).replace("\n", "<br>") for entry in item
                    )
            return result
        if isinstance(value, (list, tuple)):
            return [self.prepare(item) for item in value]
        if isinstance(value, str):
            return html.escape(value, quote=True)
        return value

    # -- 公共字段 ---------------------------------------------------------- #

    def _font_css(self) -> str:
        render = self._settings.render
        if render.font_source != "cdn" or not render.font_url:
            return ""
        return f'<link rel="stylesheet" href="{html.escape(render.font_url, quote=True)}">'

    def _font_stack(self) -> str:
        render = self._settings.render
        if render.font_source == "none":
            return _FALLBACK_STACK
        family = _UNSAFE_FONT_CHARS.sub("", render.font_family).strip()
        if not family:
            return _FALLBACK_STACK
        return f'"{family}",{_FALLBACK_STACK}'

    def _common(self) -> dict[str, Any]:
        """所有布局共享、且由插件自己生成（无需转义）的字段。"""
        return {
            "font_css": self._font_css(),
            "font_family": self._font_stack(),
            "brand": _BRAND,
            "theme_label": THEME_LABELS.get(self._settings.render.theme, ""),
            "footer": "数据来源 VNDB · TouchGal · AnimeTrace",
            "stamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "show_footer": self._settings.render.show_footer,
        }

    # -- 渲染 -------------------------------------------------------------- #

    async def render(
        self,
        layout: Layout,
        data: Mapping[str, Any],
        *,
        options: dict[str, Any] | None = None,
    ) -> str:
        """渲染并返回图片 URL。失败抛 RenderException。"""
        payload = self.prepare(dict(data))
        # 特写布局把 subtitle 当正文大标题（作品名/角色名）用，页眉里再印
        # 一遍就成了重复；其余布局的 subtitle 本来就是给页眉写的。
        payload["head_sub"] = "" if layout is Layout.FEATURE else payload.get("subtitle", "")
        payload.update(self._common())
        try:
            template = self.template(layout)
        except Exception as error:
            raise RenderException(f"模板资源不可用（{error}）") from error

        render = self._settings.render
        safe = render.safe_options
        wanted = options or (safe if self._degraded else render.render_options)

        try:
            url = await self._shoot(template, payload, wanted)
        except Exception as error:
            if wanted == safe:
                logger.error("%s 渲染 %s 布局失败：%s", _LOG, layout.value, error)
                raise RenderException(str(error)) from error
            # 清晰度参数（scale / device_scale_factor_level / viewport_width）只有
            # 较新的 t2i 服务认；老服务或公共端点可能直接拒收整个请求。这里退一步
            # 用最小参数集重试，宁可图小一点也不能整条指令没有输出。
            logger.warning("%s 高清参数被渲染服务拒绝（%s），本次会话改用兼容参数", _LOG, error)
            self._degraded = True
            try:
                url = await self._shoot(template, payload, safe)
            except Exception as again:
                logger.error("%s 渲染 %s 布局失败：%s", _LOG, layout.value, again)
                raise RenderException(str(again)) from again

        if not url:
            raise RenderException("渲染服务没有返回图片地址")
        return str(url)

    @staticmethod
    async def _shoot(template: str, payload: Mapping[str, Any], options: dict[str, Any]) -> Any:
        return await html_renderer.render_custom_template(
            template, dict(payload), return_url=True, options=options
        )

    # -- 纯文本兜底 -------------------------------------------------------- #

    @staticmethod
    def _card_text(card: Mapping[str, Any]) -> str:
        headline = " ".join(str(card[key]) for key in ("badge", "subtitle") if card.get(key))
        lines = [headline] if headline else []
        lines.extend(str(item) for item in card.get("desc") or [])
        return "\n".join(lines)

    def plain_text(self, data: Mapping[str, Any]) -> str:
        """把同一份数据降级成纯文本。

        渲染服务不可用时（网络受限、端点全挂）指令仍能给出可读结果，
        而不是像上游那样直接抛一句「渲染失败」。
        """
        blocks: list[str] = []

        header = [str(data[key]) for key in ("title", "subtitle") if data.get(key)]
        if header:
            blocks.append("\n".join(header))

        hero = data.get("hero")
        if isinstance(hero, Mapping) and hero.get("text"):
            blocks.append("\n".join(str(item) for item in hero["text"]))

        if data.get("info"):
            blocks.append("\n".join(str(item) for item in data["info"]))

        for card in data.get("cards") or []:
            if isinstance(card, Mapping):
                blocks.append(self._card_text(card))

        for section in data.get("sections") or []:
            if not isinstance(section, Mapping):
                continue
            head = section.get("header")
            if isinstance(head, Mapping):
                head = head.get("text")
            if head:
                blocks.append("\n".join(str(item) for item in head))
            for card in section.get("cards") or []:
                if isinstance(card, Mapping):
                    blocks.append(self._card_text(card))

        for group in data.get("groups") or []:
            if not isinstance(group, Mapping):
                continue
            rows = [
                f"{row.get('label', '')} {row.get('value', '')}".strip()
                for row in group.get("rows") or []
                if isinstance(row, Mapping)
            ]
            lines = [str(group.get("name") or ""), *rows]
            blocks.append("\n".join(line for line in lines if line))

        for key in ("desc", "notes"):
            if data.get(key):
                blocks.append("\n".join(str(item) for item in data[key]))

        text = "\n\n".join(block for block in blocks if block.strip())
        return text or "暂无可展示的内容。"
