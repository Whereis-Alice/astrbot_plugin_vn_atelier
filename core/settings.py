"""配置解析。

设计原则：**配置解析永远不抛异常**。

上游插件在 initialize() 里直接对配置字符串做 split / int()，一旦用户
填错格式就会让整个插件加载失败。这里把每一项都做区间收敛与格式校验：
数值超界就夹回区间，选项写错就退回默认项；而 WebUI 无法约束的自由文本
（字体地址、代理、推送时间、白名单）校验失败时会把问题记进 warnings，
由 runtime 统一写进日志，让用户知道自己填错了哪一项。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .types import CHARACTER_OPTION_KEYS, Theme

DEFAULT_FONT_URL = "https://cdn.jsdelivr.net/npm/lxgw-wenkai-webfont@1.7.0/style.css"
DEFAULT_FONT_FAMILY = "LXGW WenKai"

#: 渲染视口宽度（CSS 像素）。必须与 base.html 里 <meta name="viewport"> 的
#: width 保持一致：t2i 服务在未显式收到 viewport_width 时会去正则匹配这个
#: meta，两处不一致会让排版与实际截图宽度错位。
VIEWPORT_WIDTH = 1000

#: 设备像素比档位 → 实际倍率，仅用于日志与文档展示。
SCALE_FACTORS = {"normal": 1.0, "high": 1.3, "ultra": 1.8}

DEFAULT_TOUCHGAL_BASE = "https://www.touchgal.ink/"


# --------------------------------------------------------------------------- #
# 原始值解析helpers
# --------------------------------------------------------------------------- #


def _as_int(raw: Any, default: int, low: int | None = None, high: int | None = None) -> int:
    """把任意配置值转成整数并夹到区间内，失败时返回默认值。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _as_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _as_str(raw: Any, default: str = "") -> str:
    if raw is None:
        return default
    text = str(raw).strip()
    return text or default


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, Sequence):
        return [str(i).strip() for i in raw if str(i).strip()]
    return []


def _option_key(raw: Any, allowed: Sequence[str], default: str) -> str:
    """解析形如 starlit-星夜 / c-不筛选 的选项，取连字符前的键。"""
    text = _as_str(raw)
    key = text.split("-", 1)[0].strip() if text else ""
    return key if key in allowed else default


def _clean_clearance(raw: str) -> tuple[str, str | None]:
    """清洗手填的 cf_clearance，返回（裸值，需要提醒用户的问题）。

    实践中这一项被填错的方式高度集中在两种：一是从开发者工具里连名字一起
    复制，粘进来的是 ``cf_clearance=xxx``；二是复制了整个 Cookie 请求头，粘
    进来的是 ``cf_clearance=xxx; other=yyy``。两种都能机械地还原成裸值，所以
    这里直接改对并如实告知，而不是让用户对着一条 403 日志反复猜。
    """
    value = raw.strip().strip('"').strip("'")
    if not value:
        return "", None
    note: str | None = None
    if ";" in value:
        picked = ""
        for chunk in value.split(";"):
            item = chunk.strip()
            if item.lower().startswith("cf_clearance="):
                picked = item
                break
        if picked:
            value = picked
            note = "安全设置-cf_clearance 里粘进了整段 Cookie，已自动截出 cf_clearance 本身"
        else:
            value = value.split(";", 1)[0].strip()
            note = "安全设置-cf_clearance 含分号，已只保留第一段"
    if value.lower().startswith("cf_clearance="):
        value = value.split("=", 1)[1].strip()
        note = note or "安全设置-cf_clearance 前面带了字段名，已自动去掉"
    return value, note


def _section(config: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    """取出一个配置分组；缺失或类型不对时返回空映射。"""
    if not isinstance(config, Mapping):
        return {}
    value = config.get(name)
    return value if isinstance(value, Mapping) else {}


# --------------------------------------------------------------------------- #
# 分组
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class BasicSettings:
    """通用设置。"""

    request_retries: int = 5
    """单个请求的最大尝试次数。"""

    request_timeout: int = 30
    """单次请求的总超时（秒）。"""

    session_timeout: int = 60
    """等待用户后续输入的超时（秒）。"""

    clean_on_restart: bool = False
    forward_limit: int = 10
    """合并转发单条包含的消息数，0 表示不限制。"""

    resource_limit: int = 0
    """下载资源最多展示多少条，0 表示全部。"""

    text_fallback: bool = True
    """渲染失败时是否退回纯文本输出。"""


@dataclass(slots=True)
class RenderSettings:
    """外观与渲染设置。"""

    theme: Theme = Theme.STARLIT
    font_source: str = "cdn"
    font_url: str = DEFAULT_FONT_URL
    font_family: str = DEFAULT_FONT_FAMILY
    quality: int = 95
    show_footer: bool = True

    scale: str = "high"
    """设备像素比档位：normal(1x) / high(1.3x) / ultra(1.8x)。"""

    image_format: str = "jpeg"
    """输出格式：jpeg 体积小，png 无损但更大。"""

    @property
    def scale_factor(self) -> float:
        return SCALE_FACTORS.get(self.scale, 1.3)

    @property
    def render_options(self) -> dict[str, Any]:
        """传给 html_renderer 的截图参数。

        清晰度的关键是 scale + device_scale_factor_level 必须成对出现：
        只给档位而不把 scale 切成 "device"，截图会被重新缩回 CSS 像素，
        文件更大但一点没变清楚。viewport_width 显式给出，避免对端解析
        HTML meta 失败时退回一个更窄的默认宽度。
        """
        options: dict[str, Any] = {
            "full_page": True,
            "type": self.image_format,
            "scale": "device",
            "device_scale_factor_level": self.scale,
            "viewport_width": VIEWPORT_WIDTH,
        }
        if self.image_format != "png":
            options["quality"] = self.quality
        return options

    @property
    def safe_options(self) -> dict[str, Any]:
        """最小可用参数集。

        清晰度参数是较新的 t2i 服务才认的；老版本服务或官方公共端点遇到
        未知键可能直接 400。渲染层首次失败后会拿这一份重试，保证「画质
        提升」不会变成「彻底渲染不出来」。
        """
        return {"full_page": True, "type": "jpeg", "quality": min(self.quality, 92)}


@dataclass(slots=True)
class SafetySettings:
    """网络与风控设置。"""

    enable_nsfw: bool = False
    proxy: str = ""
    touchgal_token: str = ""
    cf_clearance: str = ""
    tls: str = "chrome136"

    browser_ua: str = ""
    """签发 cf_clearance 的那个浏览器的 User-Agent。

    cf_clearance 与「签发它的出口 IP + 当时的 User-Agent」一起被签进签名里，
    UA 换掉就等于换了个客户端，Cloudflare 会当作没有通行证。所以填 cookie 的
    人必须把配套的 UA 一起填过来，否则那串 cookie 注定用不上。
    """

    solver_url: str = ""
    """FlareSolverr 地址，用于自动通过 Cloudflare 浏览器验证。留空则关闭。"""

    touchgal_base: str = DEFAULT_TOUCHGAL_BASE
    """TouchGal 站点根地址。官方换域名时无需等插件更新，改这里即可。"""


@dataclass(slots=True)
class CharacterSettings:
    """角色额外信息开关。"""

    options: frozenset[str] = frozenset({"blood", "measure", "gender_outer"})

    def enabled(self, key: str) -> bool:
        return key in self.options


@dataclass(slots=True)
class BangumiSettings:
    enabled: bool = True
    enrich: bool = True
    token: str = ""
    timeout: int = 12
    cache_hours: int = 6
    results: int = 8
    min_votes: int = 100


@dataclass(slots=True)
class ProducerSettings:
    producer_vns: int = 9
    """每个厂商展示的作品数，0 表示全部。"""


@dataclass(slots=True)
class RecommendSettings:
    cache_size: int = 5
    """推荐指令提前渲染并缓存的结果数。"""


@dataclass(slots=True)
class FindSettings:
    results: int = 3
    """每个检测框展示的候选角色数。"""


@dataclass(slots=True)
class EventSettings:
    rating: int = 75
    """简讯过滤阈值，低于此贝叶斯评分的作品不参与。"""

    start_year: int = 1990
    """回溯的起始年份。"""


@dataclass(slots=True)
class RankingSettings:
    results: int = 12
    min_votes: int = 100


@dataclass(slots=True)
class ScheduleSettings:
    """每日推送设置。"""

    push_time: str = "07:00"
    hour: int = 7
    minute: int = 0
    content: str = "c"
    gender_filter: str = "c"
    use_subscription: bool = True
    push_list: tuple[tuple[str, str], ...] = ()
    """白名单，元素为 (平台id, 群聊id)。"""

    @property
    def cron_expression(self) -> str:
        """标准 5 段 cron 表达式：分 时 日 月 周。"""
        return f"{self.minute} {self.hour} * * *"


@dataclass(slots=True)
class CacheSettings:
    """图片缓存设置。"""

    max_size_mb: int = 256
    """磁盘缓存上限，0 表示不限制。"""

    ttl_days: int = 14
    """缓存有效期，0 表示永不过期。"""

    memory_entries: int = 64
    """内存 LRU 条数，0 表示关闭内存缓存。"""


@dataclass(slots=True)
class LimitSettings:
    """限流与扩展开关。"""

    cooldown_seconds: int = 3
    enable_llm_tools: bool = False


@dataclass(slots=True)
class Settings:
    """插件的全部配置。由 runtime 在每次加载时重新构造。"""

    basic: BasicSettings = field(default_factory=BasicSettings)
    render: RenderSettings = field(default_factory=RenderSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    bangumi: BangumiSettings = field(default_factory=BangumiSettings)
    character: CharacterSettings = field(default_factory=CharacterSettings)
    producer: ProducerSettings = field(default_factory=ProducerSettings)
    recommend: RecommendSettings = field(default_factory=RecommendSettings)
    find: FindSettings = field(default_factory=FindSettings)
    event: EventSettings = field(default_factory=EventSettings)
    ranking: RankingSettings = field(default_factory=RankingSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    limit: LimitSettings = field(default_factory=LimitSettings)
    warnings: tuple[str, ...] = ()

    # -- 解析入口 ----------------------------------------------------------- #

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> Settings:
        """从 AstrBotConfig 构造强类型配置。绝不抛异常。"""
        problems: list[str] = []

        basic_raw = _section(config, "basicSetting")
        basic = BasicSettings(
            request_retries=_as_int(basic_raw.get("requestRetries"), 5, 1, 10),
            request_timeout=_as_int(basic_raw.get("requestTimeout"), 30, 5, 180),
            session_timeout=_as_int(basic_raw.get("sessionTimeout"), 60, 10, 300),
            clean_on_restart=_as_bool(basic_raw.get("cleanOnRestart"), False),
            forward_limit=_as_int(basic_raw.get("forwardLimit"), 10, 0, 200),
            resource_limit=_as_int(basic_raw.get("resourceLimit"), 0, 0, 100),
            text_fallback=_as_bool(basic_raw.get("textFallback"), True),
        )

        render_raw = _section(config, "renderSetting")
        theme_key = _option_key(
            render_raw.get("theme"), [t.value for t in Theme], Theme.STARLIT.value
        )
        font_source = _option_key(render_raw.get("fontSource"), ["cdn", "system", "none"], "cdn")
        font_url = _as_str(render_raw.get("fontUrl"), DEFAULT_FONT_URL)
        if font_source == "cdn" and not font_url.lower().startswith(("http://", "https://")):
            problems.append("外观设置-在线字体地址不是合法的 http(s) 链接，已改用系统字体")
            font_source = "system"
        render = RenderSettings(
            theme=Theme(theme_key),
            font_source=font_source,
            font_url=font_url,
            font_family=_as_str(render_raw.get("fontFamily"), DEFAULT_FONT_FAMILY),
            quality=_as_int(render_raw.get("renderQuality"), 95, 40, 100),
            show_footer=_as_bool(render_raw.get("showFooter"), True),
            scale=_option_key(render_raw.get("renderScale"), ["normal", "high", "ultra"], "high"),
            image_format=_option_key(render_raw.get("renderFormat"), ["jpeg", "png"], "jpeg"),
        )

        safety_raw = _section(config, "safetySetting")
        proxy = _as_str(safety_raw.get("proxy"))
        if proxy and not proxy.lower().startswith(
            ("http://", "https://", "socks5://", "socks5h://")
        ):
            problems.append("安全设置-代理地址缺少协议前缀（如 http://），已忽略该代理")
            proxy = ""
        solver_url = _as_str(safety_raw.get("solverUrl"))
        if solver_url and not solver_url.lower().startswith(("http://", "https://")):
            problems.append("安全设置-验证求解器地址不是合法的 http(s) 链接，已忽略")
            solver_url = ""
        touchgal_base = _as_str(safety_raw.get("touchgalBase"), DEFAULT_TOUCHGAL_BASE)
        if not touchgal_base.lower().startswith(("http://", "https://")):
            problems.append(
                f"安全设置-TouchGal 站点地址【{touchgal_base}】缺少协议前缀，已改用默认域名"
            )
            touchgal_base = DEFAULT_TOUCHGAL_BASE
        cf_clearance, cf_note = _clean_clearance(_as_str(safety_raw.get("cfClearance")))
        if cf_note:
            problems.append(cf_note)
        if cf_clearance and (len(cf_clearance) < 40 or " " in cf_clearance):
            problems.append(
                "安全设置-cf_clearance 看起来不完整（正常是一长串不含空格的字符），请重新复制"
            )
        browser_ua = _as_str(safety_raw.get("browserUa"))
        if cf_clearance and not browser_ua:
            problems.append(
                "安全设置-填了 cf_clearance 但没填「浏览器 User-Agent」；"
                "这串 cookie 与签发它的 UA 绑定，UA 对不上一定通不过验证"
            )
        safety = SafetySettings(
            enable_nsfw=_as_bool(safety_raw.get("enableNSFW"), False),
            proxy=proxy,
            touchgal_token=_as_str(safety_raw.get("touchgalToken")),
            cf_clearance=cf_clearance,
            tls=_as_str(safety_raw.get("tls"), "chrome136"),
            browser_ua=browser_ua,
            solver_url=solver_url.rstrip("/"),
            touchgal_base=touchgal_base if touchgal_base.endswith("/") else touchgal_base + "/",
        )

        character_raw = _section(config, "characterSetting")
        bangumi_raw = _section(config, "bangumiSetting")
        bangumi = BangumiSettings(
            enabled=_as_bool(bangumi_raw.get("enabled"), True),
            enrich=_as_bool(bangumi_raw.get("enrichCards"), True),
            token=_as_str(bangumi_raw.get("accessToken")),
            timeout=_as_int(bangumi_raw.get("timeout"), 12, 3, 30),
            cache_hours=_as_int(bangumi_raw.get("cacheHours"), 6, 1, 48),
            results=_as_int(bangumi_raw.get("results"), 8, 1, 12),
            min_votes=_as_int(bangumi_raw.get("minVotes"), 100, 0, 10000),
        )
        if "characterOptions" in character_raw:
            raw_options = _as_list(character_raw.get("characterOptions"))
            keys = {
                CHARACTER_OPTION_KEYS[item.split("-", 1)[0]]
                for item in raw_options
                if item.split("-", 1)[0] in CHARACTER_OPTION_KEYS
            }
            character = CharacterSettings(options=frozenset(keys))
        else:
            # 分组整体缺失（配置还没初始化，或被手工删掉）时保留默认三项，
            # 而不是把角色卡降级成只剩名字。用户主动取消全部勾选仍会得到空集。
            character = CharacterSettings()

        producer = ProducerSettings(
            producer_vns=_as_int(_section(config, "producerSetting").get("producerVns"), 9, 0, 100)
        )
        recommend = RecommendSettings(
            cache_size=_as_int(_section(config, "recommendSetting").get("recommendCache"), 5, 1, 12)
        )
        find = FindSettings(
            results=_as_int(_section(config, "findSetting").get("findResults"), 3, 1, 10)
        )

        event_raw = _section(config, "eventSetting")
        event = EventSettings(
            rating=_as_int(event_raw.get("eventRating"), 75, 0, 100),
            start_year=_as_int(event_raw.get("eventStartYear"), 1990, 1970, 2100),
        )

        ranking_raw = _section(config, "rankingSetting")
        ranking = RankingSettings(
            results=_as_int(ranking_raw.get("rankingResults"), 12, 3, 24),
            min_votes=_as_int(ranking_raw.get("rankingMinVotes"), 100, 0, 10000),
        )

        schedule_raw = _section(config, "scheduleSetting")
        push_time = _as_str(schedule_raw.get("pushTime"), "07:00")
        hour, minute, time_problem = _parse_clock(push_time)
        if time_problem:
            problems.append(time_problem)
            push_time = "07:00"
        push_list, list_problems = _parse_push_list(_as_list(schedule_raw.get("pushList")))
        problems.extend(list_problems)
        schedule = ScheduleSettings(
            push_time=push_time,
            hour=hour,
            minute=minute,
            content=_option_key(schedule_raw.get("scheduleContent"), ["a", "b", "c"], "c"),
            gender_filter=_option_key(schedule_raw.get("genderFilter"), ["a", "b", "c"], "c"),
            use_subscription=_as_bool(schedule_raw.get("useSubscription"), True),
            push_list=push_list,
        )

        cache_raw = _section(config, "cacheSetting")
        cache = CacheSettings(
            max_size_mb=_as_int(cache_raw.get("maxSizeMB"), 256, 0, 4096),
            ttl_days=_as_int(cache_raw.get("ttlDays"), 14, 0, 365),
            memory_entries=_as_int(cache_raw.get("memoryEntries"), 64, 0, 512),
        )

        limit_raw = _section(config, "limitSetting")
        limit = LimitSettings(
            cooldown_seconds=_as_int(limit_raw.get("cooldownSeconds"), 3, 0, 120),
            enable_llm_tools=_as_bool(limit_raw.get("enableLlmTools"), False),
        )

        if safety.enable_nsfw and not safety.touchgal_token:
            problems.append("安全设置-已开启 NSFW 但未填写 TouchGal Token，站点仍会按 SFW 返回")

        return cls(
            basic=basic,
            render=render,
            safety=safety,
            bangumi=bangumi,
            character=character,
            producer=producer,
            recommend=recommend,
            find=find,
            event=event,
            ranking=ranking,
            schedule=schedule,
            cache=cache,
            limit=limit,
            warnings=tuple(problems),
        )

    def log_warnings(self) -> None:
        """把解析期收集到的配置问题写进日志。"""
        for problem in self.warnings:
            logger.warning("[Gal工房] 配置项需要检查：%s", problem)


def _parse_clock(value: str) -> tuple[int, int, str | None]:
    """解析 HH:MM。失败时返回默认 07:00 及问题描述。"""
    parts = value.replace("：", ":").split(":")
    if len(parts) != 2:
        return 7, 0, f"每日推送-推送时间格式应为 HH:MM，当前为【{value}】，已改用 07:00"
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return 7, 0, f"每日推送-推送时间含非数字字符【{value}】，已改用 07:00"
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 7, 0, f"每日推送-推送时间超出范围【{value}】，已改用 07:00"
    return hour, minute, None


def _parse_push_list(raw: Sequence[str]) -> tuple[tuple[tuple[str, str], ...], list[str]]:
    """解析「平台id-群聊id」白名单，跳过并报告非法项。"""
    parsed: list[tuple[str, str]] = []
    problems: list[str] = []
    for item in raw:
        platform, _, session = item.partition("-")
        if not platform.strip() or not session.strip():
            problems.append(f"每日推送-白名单项【{item}】格式应为「平台id-群聊id」，已跳过")
            continue
        parsed.append((platform.strip(), session.strip()))
    return tuple(parsed), problems
