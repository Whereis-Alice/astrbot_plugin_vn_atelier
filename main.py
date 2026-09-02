"""视觉小说工房（astrbot_plugin_vn_atelier）插件入口。

这个文件只做三件事：

1. 生命周期 —— 建立 / 释放 AtelierRuntime，注册 / 注销定时任务与 LLM 工具；
2. 指令映射 —— 把 AstrBot 的指令装饰器映射到 core.command 里的处理器类；
3. 异常收敛 —— 所有子指令共用同一个分发器，业务代码只管抛异常。

真正的业务逻辑都在 core/ 下，见 core/__init__.py 的分层说明。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .core.command import (
    CacheCommand,
    CharacterCommand,
    CollectAddCommand,
    CollectListCommand,
    CollectRemoveCommand,
    DailyDigest,
    DownloadCommand,
    EventCommand,
    FindCommand,
    HelpCommand,
    ProducerCommand,
    PushCommand,
    RandomCommand,
    RankingCommand,
    RecommendCommand,
    ThemeCommand,
    VnCommand,
    VndbIdCommand,
)
from .core.runtime import AtelierRuntime
from .core.service import TOOL_NAMES, build_tools
from .core.types import EarlyReturn, Tips
from .core.utils import brief

_LOG = "[视觉小说工房]"
_PLUGIN_NAME = "astrbot_plugin_vn_atelier"
_JOB_NAME = "vn_atelier:daily_digest"

_NOT_READY = "插件还没初始化完成，请稍后再试。"
_CRASHED = "出了点意外，指令没能执行完。详细原因已写进 AstrBot 日志。"


class VnAtelierPlugin(Star):
    """视觉小说工房。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.runtime: AtelierRuntime | None = None
        self._job_ids: list[str] = []
        self._tools_registered = False

    # -- 生命周期 ---------------------------------------------------------- #

    async def initialize(self) -> None:
        """建立运行时、定时任务与 LLM 工具。"""
        runtime = AtelierRuntime(
            self.config,
            self,
            StarTools.get_data_dir(_PLUGIN_NAME),
            Path(__file__).parent / "resources",
        )
        await runtime.initialize()
        self.runtime = runtime

        if runtime.settings.basic.clean_on_restart:
            removed, freed = await runtime.cache.sweep()
            logger.info("%s 启动清理：删除 %s 个缓存文件，释放 %s 字节", _LOG, removed, freed)

        await self._register_job()
        self._register_tools()
        logger.info("%s 已就绪，主指令「工房」，发送「工房 帮助」查看指令表", _LOG)

    async def terminate(self) -> None:
        """卸载 / 停用插件时释放全部资源。"""
        await self._remove_jobs(self._job_ids)
        self._job_ids.clear()
        self._unregister_tools()
        if self.runtime is not None:
            await self.runtime.terminate()
            self.runtime = None
        logger.info("%s 已卸载", _LOG)

    # -- 定时任务 ---------------------------------------------------------- #

    @property
    def _cron(self) -> Any | None:
        """cron_manager 在部分部署形态下可能尚未装配，取不到就跳过定时功能。"""
        return getattr(self.context, "cron_manager", None)

    async def _remove_jobs(self, job_ids: list[str]) -> None:
        manager = self._cron
        if manager is None:
            return
        for job_id in job_ids:
            try:
                await manager.delete_job(job_id)
            except Exception as error:
                logger.warning("%s 删除定时任务 %s 失败：%s", _LOG, job_id, error)

    async def _register_job(self) -> None:
        """注册每日简讯任务。

        add_basic_job 每次调用都会分配一个新的 job_id，热重载若不先清掉同名
        旧任务，就会出现「一天推送好几遍」。所以这里先按任务名去重。
        """
        runtime = self.runtime
        manager = self._cron
        if runtime is None or manager is None:
            return

        try:
            existing = await manager.list_jobs(job_type="basic")
            stale = [job.job_id for job in existing if getattr(job, "name", "") == _JOB_NAME]
            await self._remove_jobs(stale)
        except Exception as error:
            logger.warning("%s 清理旧定时任务失败：%s", _LOG, error)

        schedule = runtime.settings.schedule
        try:
            job = await manager.add_basic_job(
                name=_JOB_NAME,
                cron_expression=schedule.cron_expression,
                handler=self._push_today,
                description="视觉小说工房 · 每日简讯推送",
                enabled=True,
                persistent=False,
            )
        except Exception as error:
            logger.error("%s 注册每日简讯任务失败：%s", _LOG, error)
            return
        self._job_ids.append(job.job_id)
        logger.info(
            "%s 每日简讯已排程：%s（%s）", _LOG, schedule.push_time, schedule.cron_expression
        )

    async def _push_today(self) -> None:
        """定时任务处理器：整份简讯只渲染一次，再分发给所有目标会话。"""
        runtime = self.runtime
        if runtime is None:
            return

        targets = await runtime.subscription.targets()
        if not targets:
            logger.debug("%s 没有推送目标，跳过本次简讯", _LOG)
            return

        try:
            urls = await DailyDigest(runtime).build()
        except Exception as error:
            logger.error("%s 生成每日简讯失败：%s", _LOG, error)
            return
        if not urls:
            logger.warning("%s 每日简讯没有可推送的内容", _LOG)
            return

        sent = 0
        for target in targets:
            for url in urls:
                try:
                    ok = await self.context.send_message(target, MessageChain().url_image(url))
                except Exception as error:
                    logger.warning("%s 推送到 %s 失败：%s", _LOG, target, error)
                    break
                if not ok:
                    logger.warning("%s 推送到 %s 未送达，可能会话已失效", _LOG, target)
                    break
                sent += 1
        logger.info("%s 每日简讯已推送 %s 条（目标 %s 个）", _LOG, sent, len(targets))

    # -- LLM 工具 ---------------------------------------------------------- #

    def _register_tools(self) -> None:
        runtime = self.runtime
        if runtime is None or not runtime.settings.limit.enable_llm_tools:
            return
        try:
            self.context.add_llm_tools(*build_tools(runtime))
        except Exception as error:
            logger.warning("%s 注册 LLM 工具失败：%s", _LOG, error)
            return
        self._tools_registered = True
        logger.info("%s 已注册 %s 个 LLM 工具", _LOG, len(TOOL_NAMES))

    def _unregister_tools(self) -> None:
        if not self._tools_registered:
            return
        for name in TOOL_NAMES:
            try:
                self.context.unregister_llm_tool(name)
            except Exception as error:
                logger.warning("%s 注销 LLM 工具 %s 失败：%s", _LOG, name, error)
        self._tools_registered = False

    # -- 统一分发 ---------------------------------------------------------- #

    async def _run(
        self,
        event: AstrMessageEvent,
        factory: Any,
        value: str = "",
    ) -> AsyncIterator[MessageEventResult]:
        """执行一条子指令，并把异常收敛成用户可读的提示。

        异常必须在这个生成器内部处理完：如果改成「调用一个返回
        MessageEventResult 的辅助函数」，处理器中途 yield 过的内容就会与
        错误提示错位。
        """
        runtime = self.runtime
        if runtime is None:
            yield event.plain_result(_NOT_READY)
            return

        try:
            if factory.cooldown:
                runtime.cooldown.check(f"{event.unified_msg_origin}::{event.get_sender_id()}")
            async for result in factory(runtime, event).execute(value):
                yield result
        except EarlyReturn:
            # 处理器已经自己发过消息（会话式交互），不需要再补一条。
            return
        except Tips as error:
            yield event.plain_result(brief(error))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s 指令 %s 执行失败", _LOG, factory.__name__)
            yield event.plain_result(_CRASHED)

    # -- 指令 -------------------------------------------------------------- #

    @filter.command_group("工房", alias={"vna", "VNA", "gal工房"})
    async def atelier(self, event: AstrMessageEvent):
        """视觉小说工房主指令，发送「工房 帮助」查看全部子指令。"""

    @atelier.command("作品", alias={"游戏", "vn"})
    async def vn(self, event: AstrMessageEvent, keyword: str = ""):
        """按关键词检索视觉小说作品。"""
        async for result in self._run(event, VnCommand, keyword):
            yield result

    @atelier.command("角色", alias={"人物", "character"})
    async def character(self, event: AstrMessageEvent, keyword: str = ""):
        """按关键词检索视觉小说角色。"""
        async for result in self._run(event, CharacterCommand, keyword):
            yield result

    @atelier.command("厂商", alias={"作者", "producer"})
    async def producer(self, event: AstrMessageEvent, keyword: str = ""):
        """按关键词检索会社 / 社团，并列出代表作。"""
        async for result in self._run(event, ProducerCommand, keyword):
            yield result

    @atelier.command("ID", alias={"id"})
    async def vndb_id(self, event: AstrMessageEvent, keyword: str = ""):
        """用 VNDB ID 精确查询，例如 v17 / c123 / p45。"""
        async for result in self._run(event, VndbIdCommand, keyword):
            yield result

    @atelier.command("简讯", alias={"event", "今日"})
    async def event_of_day(self, event: AstrMessageEvent, date: str = ""):
        """查当天（或指定「月-日」）的发售纪念日与角色生日。"""
        async for result in self._run(event, EventCommand, date):
            yield result

    @atelier.command("排行", alias={"榜单", "ranking"})
    async def ranking(self, event: AstrMessageEvent, condition: GreedyStr = ""):
        """按 VNDB 评分出榜，可附年份与标签，例如「排行 2016 百合」。"""
        async for result in self._run(event, RankingCommand, str(condition)):
            yield result

    @atelier.command("随机", alias={"random", "抽一部"})
    async def random_vn(self, event: AstrMessageEvent):
        """随机抽一部作品，附简介与游戏截图。"""
        async for result in self._run(event, RandomCommand):
            yield result

    @atelier.command("推荐", alias={"标签", "recommend"})
    async def recommend(self, event: AstrMessageEvent, tags: GreedyStr = ""):
        """按标签连续推荐，回复「换一个」继续，回复「结束」停止。"""
        async for result in self._run(event, RecommendCommand, str(tags)):
            yield result

    @atelier.command("下载", alias={"资源", "download"})
    async def download(self, event: AstrMessageEvent, the_id: str = ""):
        """查询 TouchGal 上的资源链接。"""
        async for result in self._run(event, DownloadCommand, the_id):
            yield result

    @atelier.command("出处", alias={"识别", "find"})
    async def find(self, event: AstrMessageEvent, url: str = ""):
        """按图识别角色出处，可直接发图、引用图片或给出图片链接。"""
        async for result in self._run(event, FindCommand, url):
            yield result

    @atelier.command("收藏", alias={"mark"})
    async def collect(self, event: AstrMessageEvent, keyword: str = ""):
        """把某个 VNDB ID 加入个人收藏夹。"""
        async for result in self._run(event, CollectAddCommand, keyword):
            yield result

    @atelier.command("取消收藏", alias={"unmark"})
    async def uncollect(self, event: AstrMessageEvent, keyword: str = ""):
        """把某个 VNDB ID 从个人收藏夹移除。"""
        async for result in self._run(event, CollectRemoveCommand, keyword):
            yield result

    @atelier.command("清单", alias={"收藏夹", "collection"})
    async def collection(self, event: AstrMessageEvent, keyword: str = ""):
        """查看个人收藏夹，发送「清单 清空」可清空。"""
        async for result in self._run(event, CollectListCommand, keyword):
            yield result

    @atelier.command("帮助", alias={"help", "菜单"})
    async def help_card(self, event: AstrMessageEvent):
        """展示全部子指令。"""
        async for result in self._run(event, HelpCommand):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @atelier.command("主题", alias={"theme"})
    async def theme(self, event: AstrMessageEvent, name: str = ""):
        """切换卡片配色并写回配置（管理员）。"""
        async for result in self._run(event, ThemeCommand, name):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @atelier.command("缓存", alias={"cache"})
    async def cache(self, event: AstrMessageEvent, action: str = ""):
        """查看或清理图片缓存（管理员）。"""
        async for result in self._run(event, CacheCommand, action):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @atelier.command("推送", alias={"subscribe"})
    async def push(self, event: AstrMessageEvent, action: str = ""):
        """订阅或退订本会话的每日简讯（管理员）。"""
        async for result in self._run(event, PushCommand, action):
            yield result
