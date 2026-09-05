"""离线自检：不联网、不需要运行中的 AstrBot 实例。

只回答一个问题——改完代码之后，这个插件还能不能正常出图。

    1. 所有模块都能被导入（决定插件能否被 AstrBot 加载）
    2. 模板占位符仍然唯一，四主题 + 四布局资源齐全
    3. 配置解析对任意垃圾输入都不抛异常，且非法值会收敛到合法区间
    4. prepare() 真的转义了用户数据，并按契约派生 <key>_html
    5. 四布局 x 四主题共 16 组模板都能渲染出完整 HTML

用法::

    pytest tests
    python tests/test_smoke.py   # 不装 pytest 也能跑
"""

from __future__ import annotations

import dataclasses
import html
import importlib
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PKG = "vn_atelier_selftest"


def _load_package() -> types.ModuleType:
    """把插件根目录挂成一个固定名字的包。

    插件目录名可能是 astrbot_plugin_vn_atelier，也可能是从 GitHub 下载 zip
    得到的 astrbot_plugin_vn_atelier-main（后者不是合法的 Python 标识符），
    所以这里不依赖目录名，手工造一个命名空间包，包内的相对导入照常工作。
    """
    if PKG not in sys.modules:
        package = types.ModuleType(PKG)
        package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
        sys.modules[PKG] = package
    return sys.modules[PKG]


_load_package()

core_types = importlib.import_module(PKG + ".core.types")
core_settings = importlib.import_module(PKG + ".core.settings")
core_renderer = importlib.import_module(PKG + ".core.service.renderer")
core_base = importlib.import_module(PKG + ".core.command.base")
core_text = importlib.import_module(PKG + ".core.utils.text")

Layout = core_types.Layout
Theme = core_types.Theme
Settings = core_settings.Settings
RenderSettings = core_settings.RenderSettings
Renderer = core_renderer.Renderer

RESOURCES = ROOT / "resources"

#: 一段假的 base64 图片。只要以 data: 开头就够了，测试不解码它。
IMG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAg="


# --------------------------------------------------------------------------- #
# 1. 模块导入
# --------------------------------------------------------------------------- #


def test_all_modules_import() -> None:
    """任何一个模块导入失败，插件在 AstrBot 里就是直接加载失败。"""
    failures: list[str] = []
    for file in sorted(ROOT.rglob("*.py")):
        if "tests" in file.relative_to(ROOT).parts:
            continue
        name = PKG + "." + ".".join(file.relative_to(ROOT).with_suffix("").parts)
        try:
            importlib.import_module(name)
        except Exception as error:  # 收集全部失败，最后一次性报出
            failures.append(name + " -> " + repr(error))
    assert not failures, "以下模块无法导入：\n" + "\n".join(failures)


# --------------------------------------------------------------------------- #
# 2. 资源与模板契约
# --------------------------------------------------------------------------- #


def test_template_marks_are_unique() -> None:
    """renderer 用字符串替换装配模板，占位符出现两次就会装配出坏 HTML。"""
    base = (RESOURCES / "template" / "base.html").read_text(encoding="utf-8")
    assert base.count(core_renderer._THEME_MARK) == 1
    assert base.count(core_renderer._BODY_MARK) == 1


def test_shiki_skip_marker_kept() -> None:
    """这个字面量一旦被删掉，t2i 端点就会往每张图里注入 1.2MB 高亮运行时。"""
    base = (RESOURCES / "template" / "base.html").read_text(encoding="utf-8")
    assert "astrbot-t2i-shiki-runtime" in base


def test_all_themes_and_layouts_present() -> None:
    for theme in Theme:
        assert (RESOURCES / "theme" / (theme.value + ".css")).is_file(), theme.value
    for layout in Layout:
        name = "layout_" + layout.value + ".html"
        assert (RESOURCES / "template" / name).is_file(), name


# --------------------------------------------------------------------------- #
# 3. 配置解析
# --------------------------------------------------------------------------- #

GARBAGE: dict[str, Any] = {
    "basicSetting": {"requestRetries": "abc", "requestTimeout": -99, "forwardLimit": 10**9},
    "renderSetting": {
        "theme": "不存在的主题",
        "fontSource": "???",
        "fontUrl": "ftp://example.com/f.css",
        "renderQuality": 999,
        "fontFamily": 'Evil";}<script>',
    },
    "safetySetting": {"proxy": "1.2.3.4:8080", "enableNSFW": True, "touchgalToken": ""},
    "characterSetting": {"characterOptions": ["z-未知选项", 123]},
    "rankingSetting": {"rankingResults": 0, "rankingMinVotes": -5},
    "eventSetting": {"eventRating": "high", "eventStartYear": 1},
    "scheduleSetting": {
        "pushTime": "99:99",
        "pushList": ["坏格式", None],
        "scheduleContent": "zzz",
        "genderFilter": None,
    },
    "cacheSetting": {"maxSizeMB": "x"},
    "limitSetting": {"cooldownSeconds": 10**9},
}


def test_from_config_never_raises() -> None:
    """配置解析必须永不抛异常，否则用户填错一个字就整个插件加载失败。"""
    payloads: tuple[Any, ...] = (
        None,
        {},
        GARBAGE,
        {"renderSetting": "本该是字典"},
        {"theme": "sakura"},  # 扁平结构，应被整体忽略
    )
    for payload in payloads:
        assert Settings.from_config(payload) is not None


def test_garbage_values_converge() -> None:
    settings = Settings.from_config(GARBAGE)
    assert settings.render.theme is Theme.STARLIT
    assert 1 <= settings.basic.request_retries <= 10
    assert settings.basic.forward_limit == 200, "超上限应夹到 200"
    assert 40 <= settings.render.quality <= 100
    assert settings.ranking.results == 3, "低于下限应夹到 3"
    assert settings.limit.cooldown_seconds == 120
    assert settings.schedule.push_time == "07:00"
    assert (settings.schedule.hour, settings.schedule.minute) == (7, 0)
    assert settings.schedule.push_list == (), "格式错误的白名单项应被丢弃"
    assert settings.render.font_source == "system", "字体地址非法应改用系统字体"
    assert settings.safety.proxy == "", "缺协议前缀的代理应被忽略"
    assert settings.character.options == frozenset(), "未知的角色选项应被过滤掉"


def test_bad_config_is_reported() -> None:
    """自由文本填错时要留下 warning，否则用户不知道自己哪里填错了。"""
    warnings = Settings.from_config(GARBAGE).warnings
    assert warnings
    assert any("字体" in item for item in warnings)
    assert any("代理" in item for item in warnings)
    assert any("TouchGal" in item for item in warnings)


def test_missing_section_keeps_defaults() -> None:
    """分组整体缺失时要保留默认值，而不是把角色卡降级成只剩名字。"""
    settings = Settings.from_config({"basicSetting": {}})
    assert len(settings.character.options) == 3
    assert settings.render.theme is Theme.STARLIT


def test_font_family_injection_is_sanitised() -> None:
    """字体名会被拼进 CSS，引号和分号必须先清掉。"""
    renderer = Renderer(Settings.from_config(GARBAGE), RESOURCES)
    stack = renderer._font_stack()
    assert not set(stack[: stack.find(",")]) & set("<>;{}")


# --------------------------------------------------------------------------- #
# 4. 数据预处理
# --------------------------------------------------------------------------- #


def test_prepare_escapes_and_derives_html() -> None:
    renderer = Renderer(Settings(), RESOURCES)
    prepared = renderer.prepare(
        {
            "title": "<b>粗体</b>",
            "desc": ["第一行<i>斜体</i>", "第二行 &"],
            "previews": [IMG, IMG],
            "count": 5,
        }
    )
    assert prepared["title"] == html.escape("<b>粗体</b>", quote=True)
    assert "desc_html" in prepared, "短字符串列表应派生 <key>_html"
    assert "&lt;i&gt;" in prepared["desc_html"], "派生的 HTML 里原始标签必须已转义"
    assert "<br>" in prepared["desc_html"]
    assert "previews_html" not in prepared, "base64 列表不应再拼一份副本"
    assert prepared["count"] == 5, "非字符串应原样透传"


# --------------------------------------------------------------------------- #
# 5. 十六组模板真渲染
# --------------------------------------------------------------------------- #


def _card(index: int) -> dict[str, Any]:
    return core_base.card(
        image=IMG,
        subtitle="作品 " + str(index),
        desc=["评分 8." + str(index), "2024-0" + str(index) + "-01"],
        badge="#" + str(index),
    )


def _payloads() -> dict[str, dict[str, Any]]:
    """每个布局一份数据，刻意混入「只给必填键」的条目。

    core/command/base.py 里的构造函数会省略空的可选键（没有 badge 就不写
    badge），所以模板必须容忍缺键——官方 t2i 端点用的是 Jinja 默认的宽松
    Undefined，下面的 Environment 保持一致。
    """
    return {
        "gallery": core_base.gallery(
            cards=[_card(1), _card(2), {"image": IMG}],
            title="检索结果",
            subtitle="关键词：星空",
        ),
        "dossier": core_base.dossier(
            sections=[
                core_base.section(text=["会社 A", "成立于 2001"], image=IMG, cards=[_card(1)]),
                core_base.section(text=["会社 B"]),
            ],
            title="厂商档案",
            hero={"image": IMG, "text": ["识别模型「A」"]},
            notes=("数据来自 VNDB。",),
        ),
        "feature": core_base.feature(
            main_image=IMG,
            subtitle="某部作品",
            info=["发售 2024-05-24", "评分 8.7"],
            title="今日推荐",
            desc=["一段简介。"],
            previews=[IMG, IMG],
            cards_title="主要角色",
            cards=[_card(1)],
        ),
        "panel": core_base.panel(
            groups=[{"name": "检索", "rows": [core_base.row("工房 作品", "按关键词查作品")]}],
            title="帮助",
            subtitle="指令一览",
        ),
    }


NEEDLES = {
    "gallery": "作品 1",
    "dossier": "会社 A",
    "feature": "某部作品",
    "panel": "工房 作品",
}


def test_every_layout_and_theme_renders() -> None:
    from jinja2 import Environment

    # autoescape 关掉是为了复刻 t2i 端点的行为：转义已经在 prepare() 里做完，
    # 模板统一用 {{ x | safe }} 输出，这里再转一次会把标签原样打印出来。
    env = Environment(autoescape=False)
    payloads = _payloads()
    failures: list[str] = []

    for theme in Theme:
        settings = dataclasses.replace(
            Settings(), render=dataclasses.replace(RenderSettings(), theme=theme)
        )
        renderer = Renderer(settings, RESOURCES)
        for layout in Layout:
            label = theme.value + "/" + layout.value
            try:
                template = renderer.template(layout)
                assert core_renderer._THEME_MARK not in template, "主题占位符没被替换"
                assert core_renderer._BODY_MARK not in template, "正文占位符没被替换"

                data = dict(renderer._common())
                data.update(renderer.prepare(dict(payloads[layout.value])))
                data["head_sub"] = "" if layout is Layout.FEATURE else data.get("subtitle", "")
                output = env.from_string(template).render(**data)

                assert "{" + "{" not in output, "还有没渲染掉的 Jinja 表达式"
                assert "{" + "%" not in output, "还有没渲染掉的 Jinja 语句"
                assert output.rstrip().endswith("</html>"), "HTML 不完整"
                assert NEEDLES[layout.value] in output, "数据没进到输出里"
            except Exception as error:  # 收集全部失败，最后一次性报出
                failures.append(label + ": " + repr(error))

    assert not failures, "以下组合渲染失败：\n" + "\n".join(failures)


def test_user_data_cannot_break_out_of_the_page() -> None:
    """用户可控字段里的标签必须以纯文本呈现。"""
    from jinja2 import Environment

    env = Environment(autoescape=False)
    renderer = Renderer(Settings(), RESOURCES)
    payload = core_base.gallery(
        cards=[core_base.card(image=IMG, subtitle="<script>alert(1)</script>")],
        title="</style><script>alert(2)</script>",
    )
    data = dict(renderer._common())
    data.update(renderer.prepare(payload))
    data["head_sub"] = data.get("subtitle", "")
    output = env.from_string(renderer.template(Layout.GALLERY)).render(**data)

    assert "<script>alert(1)</script>" not in output
    assert "</style><script>" not in output
    assert "&lt;script&gt;" in output


# --------------------------------------------------------------------------- #
# 6. 清晰度与风控相关设置
# --------------------------------------------------------------------------- #


def test_render_options_carry_device_scale() -> None:
    """scale 与档位必须成对出现，只给档位的话截图只会变大、不会变清。"""
    options = RenderSettings().render_options
    assert options["scale"] == "device"
    assert options["device_scale_factor_level"] == "high"
    assert options["viewport_width"] == core_settings.VIEWPORT_WIDTH
    assert options["type"] == "jpeg"
    assert options["quality"] == RenderSettings().quality
    png = dataclasses.replace(RenderSettings(), image_format="png").render_options
    assert "quality" not in png, "PNG 不接受 quality 参数"


def test_safe_options_are_a_real_fallback() -> None:
    """降级参数必须与高清参数不同，否则失败后重试一次毫无意义。"""
    render = RenderSettings()
    assert render.safe_options != render.render_options
    assert set(render.safe_options) <= {"full_page", "type", "quality"}, "降级参数不能再带新键"
    assert render.safe_options["quality"] <= 92


def test_scale_options_parse_from_schema_labels() -> None:
    """配置界面存回来的是「键-中文说明」，解析必须认得这种形式。"""
    picked = Settings.from_config(
        {"renderSetting": {"renderScale": "ultra-超清（1.8x）", "renderFormat": "png-PNG（无损）"}}
    )
    assert picked.render.scale == "ultra"
    assert picked.render.scale_factor == 1.8
    assert picked.render.image_format == "png"

    fallback = Settings.from_config(
        {"renderSetting": {"renderScale": "巨清", "renderFormat": "webp"}}
    )
    assert (fallback.render.scale, fallback.render.image_format) == ("high", "jpeg")
    assert fallback.render.scale_factor == 1.3


def test_challenge_settings_are_normalised() -> None:
    """求解器地址与站点地址都会被拼进 URL，格式必须先收敛。"""
    good = Settings.from_config(
        {
            "safetySetting": {
                "solverUrl": "http://127.0.0.1:8191/",
                "touchgalBase": "https://gal.example.com",
            }
        }
    )
    assert good.safety.solver_url == "http://127.0.0.1:8191", "尾斜杠要去掉，否则拼出双斜杠"
    assert good.safety.touchgal_base == "https://gal.example.com/", "站点根地址要补尾斜杠"

    bad = Settings.from_config(
        {"safetySetting": {"solverUrl": "127.0.0.1:8191", "touchgalBase": "touchgal.ink"}}
    )
    assert bad.safety.solver_url == "", "缺协议前缀的求解器地址应被忽略"
    assert bad.safety.touchgal_base == core_settings.DEFAULT_TOUCHGAL_BASE
    assert any("求解器" in item for item in bad.warnings)


def test_plain_markup_strips_vndb_bbcode() -> None:
    """VNDB 简介带 BBCode：方括号不能露到图上，剧透块更不能留。"""
    text = core_text.plain_markup(
        "开场[b]很平静[/b]，[spoiler]女主早就死了[/spoiler]，"
        "详见 [url=https://vndb.org/v11]原作[/url]。"
    )
    assert "[" not in text
    assert "]" not in text
    assert "女主" not in text, "剧透块必须整段删掉"
    assert "很平静" in text
    assert "原作" in text, "链接应压成可读文字而不是被整段丢弃"


def test_command_exceptions_accept_enum_and_str() -> None:
    """异常都在错误路径上构造，本身绝不能再抛异常。"""
    kinds = (
        core_types.InvalidArgsException,
        core_types.SessionTimeoutException,
        core_types.ArgsOrNullException,
        core_types.NoResultException,
    )
    command = core_types.CommandType.RANDOM
    for kind in kinds:
        for given in (command, command.value):
            error = kind(given, "关键词")
            assert command.value in str(error), kind.__name__
            summary = core_text.brief(error)
            assert summary, kind.__name__
            assert "关键词" not in summary, "技术细节不该抛给用户"


def test_viewport_meta_matches_render_width() -> None:
    """t2i 端点用正则从 meta 里读宽度，引号和数字都不能改。"""
    base = (RESOURCES / "template" / "base.html").read_text(encoding="utf-8")
    assert ('content="width=' + str(core_settings.VIEWPORT_WIDTH)) in base


# --------------------------------------------------------------------------- #
# 不装 pytest 也能跑
# --------------------------------------------------------------------------- #


def _main() -> int:
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failed = 0
    for name, test in tests:
        try:
            test()
        except Exception as error:  # 迷你 runner 要把每一项的失败都打出来
            failed += 1
            print("FAIL " + name + "\n     " + str(error).replace("\n", "\n     "))
        else:
            print("PASS " + name)
    print("-" * 60)
    print("共 " + str(len(tests)) + " 项，失败 " + str(failed) + " 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
