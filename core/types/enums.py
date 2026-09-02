"""枚举与静态常量。

本模块只依赖标准库，可被任意子模块安全导入，不会产生循环引用。
"""

from __future__ import annotations

from enum import Enum
from typing import Final, TypeAlias

Base64: TypeAlias = str
"""base64 图片字符串，可能带 data URI 前缀。"""


class CommandType(str, Enum):
    """子指令类型。参与异常文案、布局选择与缓存分组。"""

    VN = "vn"
    CHARACTER = "character"
    PRODUCER = "producer"
    ID = "id"
    EVENT = "event"
    RANDOM = "random"
    DOWNLOAD = "download"
    FIND = "find"
    RECOMMEND = "recommend"
    EVENT_TIMED = "event_timed"
    RANKING = "ranking"
    COLLECTION = "collection"
    HELP = "help"

    def __str__(self) -> str:
        return self.value


class Layout(str, Enum):
    """渲染布局。每个布局对应 resources/template 下的一个 layout_*.html。"""

    GALLERY = "gallery"
    """网格画廊：多结果并列，用于作品/角色搜索与排行榜。"""

    DOSSIER = "dossier"
    """档案分栏：带小节标题的分组列表，用于厂商、简讯、出处识别。"""

    FEATURE = "feature"
    """特辑详情：大主视觉 + 简介正文 + 预览图，用于随机、推荐、每日推送。"""

    PANEL = "panel"
    """面板：纯文本分组表格，用于帮助卡、收藏清单、缓存统计。"""

    def __str__(self) -> str:
        return self.value


class Theme(str, Enum):
    """配色主题。每个主题对应 resources/theme 下的一个 CSS。"""

    STARLIT = "starlit"
    SAKURA = "sakura"
    CELADON = "celadon"
    NOIR = "noir"

    def __str__(self) -> str:
        return self.value


class CacheGroup(str, Enum):
    """图片缓存分组，对应缓存目录下的子目录名。"""

    VNDB = "vndb"
    TOUCHGAL = "touchgal"

    def __str__(self) -> str:
        return self.value


THEME_LABELS: Final[dict[Theme, str]] = {
    Theme.STARLIT: "星夜",
    Theme.SAKURA: "樱色",
    Theme.CELADON: "青瓷",
    Theme.NOIR: "墨影",
}
"""主题的中文展示名，用于「工房 主题」指令与帮助卡。"""

LAYOUT_OF_COMMAND: Final[dict[CommandType, Layout]] = {
    CommandType.VN: Layout.GALLERY,
    CommandType.CHARACTER: Layout.GALLERY,
    CommandType.RANKING: Layout.GALLERY,
    CommandType.PRODUCER: Layout.DOSSIER,
    CommandType.EVENT: Layout.DOSSIER,
    CommandType.FIND: Layout.DOSSIER,
    CommandType.RANDOM: Layout.FEATURE,
    CommandType.RECOMMEND: Layout.FEATURE,
    CommandType.EVENT_TIMED: Layout.FEATURE,
    CommandType.ID: Layout.GALLERY,
    CommandType.COLLECTION: Layout.PANEL,
    CommandType.HELP: Layout.PANEL,
    CommandType.DOWNLOAD: Layout.PANEL,
}
"""指令到默认布局的映射。ID 指令在拿到简介后会临时切换到 FEATURE。"""

VNDB_FIELDS: Final[dict[str, str]] = {
    "vn": (
        "id,average,rating,released,length_minutes,platforms,aliases,"
        "developers{id,original,name},titles{lang,title,official},"
        "image{url},alttitle,title"
    ),
    "character": (
        "id,name,aliases,sex,birthday,waist,hips,bust,blood_type,weight,"
        "height,cup,original,image{url},vns{id,alttitle,title}"
    ),
    "producer": "id,name,original,aliases,lang,type",
    "vn_short": "id,alttitle,title,released,rating,image{url}",
    "character_short": "id,name,original,aliases,image{url},vns{id,alttitle,title}",
    "character_event": "id,name,aliases,birthday,original,image{url}",
}
"""VNDB Kana API 的 fields 参数。字段列表直接决定响应模型可用的属性。"""

ID_PREFIX_TO_COMMAND: Final[dict[str, CommandType]] = {
    "v": CommandType.VN,
    "c": CommandType.CHARACTER,
    "p": CommandType.PRODUCER,
}
"""VNDB ID 首字母到实体类型的映射，例如 v17 是作品、c123 是角色。"""

LANG_NAMES: Final[dict[str, str]] = {
    "ja": "日文",
    "en": "英文",
    "zh-Hans": "简中",
    "zh-Hant": "繁中",
    "zh": "中文",
}

DEVELOPER_TYPES: Final[dict[str, str]] = {
    "co": "公司",
    "in": "个人",
    "ng": "业余团体",
}

GENDERS: Final[dict[str, str]] = {
    "m": "男性",
    "f": "女性",
    "b": "双性",
    "n": "无性",
}

JA_WEEKDAYS: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")
"""日式星期简称，索引与 datetime.weekday() 一致（0 为周一）。"""

MIME_TYPES: Final[dict[str, str]] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "avif": "image/avif",
    "gif": "image/gif",
    "ttf": "font/ttf",
}
"""文件后缀到 MIME 类型的映射，用于拼装 data URI。"""

CHARACTER_OPTION_KEYS: Final[dict[str, str]] = {
    "a": "blood",
    "b": "measure",
    "c": "gender_outer",
    "d": "gender_inner",
    "e": "bwh",
    "f": "cup",
}
"""角色额外信息开关：配置项形如 a-血型，取首字母作为键。"""
