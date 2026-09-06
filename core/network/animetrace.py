"""AnimeTrace 图片识别客户端。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from ..settings import Settings
from ..types import AnimeTraceResponse, ResponseException, Tips
from ..utils import FileIO
from .http import Http

_LOG = "[Gal工房]"

_SEARCH_URL = "https://api.animetrace.com/v1/search"
_MODEL_URL = "https://api.animetrace.com/v1/model/list"

_FALLBACK_MODEL = "full_game_model_kira"
"""模型列表接口不可用时的兜底模型，面向 galgame 立绘。"""

_MODEL_EXPIRED = 17703
"""模型已下线的业务错误码，需要重新选模型再试一次。"""

_SUCCESS_CODES = frozenset({0, 200})


class AnimeTrace:
    """按图找角色。"""

    def __init__(self, http: Http, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._model: str | None = None
        self._lock = asyncio.Lock()

    async def model(self, *, refresh: bool = False) -> str:
        """获取可用模型名，结果会缓存。

        上游在插件 initialize() 阶段就去请求模型列表，一旦接口抖动整个插件
        都装不上；而且那段 for / else 写反了（循环里 return 之后 else 永远
        不会执行），列表为空时还会直接下标一个可能不存在的字段。
        """
        if self._model and not refresh:
            return self._model
        async with self._lock:
            if not self._model or refresh:
                self._model = await self._fetch_model()
        return self._model

    async def _fetch_model(self) -> str:
        try:
            response = await self._http.get(_MODEL_URL, res_type="json")
        except Tips as error:
            logger.warning("%s 拉取识别模型列表失败，改用内置模型：%s", _LOG, error)
            return _FALLBACK_MODEL

        entries = response.get("data") if isinstance(response, dict) else None
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("enabled") and entry.get("id"):
                return str(entry["id"])
        logger.warning("%s 模型列表中没有可用项，改用内置模型 %s", _LOG, _FALLBACK_MODEL)
        return _FALLBACK_MODEL

    async def request_find(
        self, image: str, *, retried: bool = False
    ) -> tuple[AnimeTraceResponse, str]:
        """识别图片中的角色。

        Args:
            image: http(s) 图片地址，或 base64 字符串（可带 data URI 前缀）。
            retried: 内部使用，标记这是换模型后的第二次尝试。

        Returns:
            识别响应，以及本次实际使用的模型名。
        """
        model = await self.model()
        is_url = image.lower().startswith(("http://", "https://"))
        payload: dict[str, object] = {"model": model, "ai_detect": 1}
        payload["url" if is_url else "base64"] = image if is_url else FileIO.strip_prefix(image)

        raw = await self._http.post(_SEARCH_URL, payload, res_type="json")
        if not isinstance(raw, dict):
            raise ResponseException(_SEARCH_URL)

        code = int(raw.get("code") or 0)
        if code == _MODEL_EXPIRED and not retried:
            logger.warning("%s 模型 %s 已下线，重新选择模型后重试", _LOG, model)
            await self.model(refresh=True)
            return await self.request_find(image, retried=True)
        if code not in _SUCCESS_CODES:
            raise Tips(str(raw.get("zh_message") or "图片识别失败。"))
        return AnimeTraceResponse.model_validate(raw), model
