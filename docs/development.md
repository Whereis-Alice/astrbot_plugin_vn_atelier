# 开发与自检

[返回首页](../README.md) · [数据来源](data-sources.md)

仓库自带离线自检，不访问第三方站点，也不需要运行中的 AstrBot 实例。请在已安装 AstrBot 及插件依赖的 Python 环境中，从仓库根目录执行：

```bash
python tests/test_smoke.py     # 也可以用 pytest tests
python tests/test_bangumi.py   # Bangumi 数据、匹配、HTTP 及推送管线的离线回归
python tests/test_docs.py      # 文档链接、工具清单与 README 内容边界
python tests/test_daily.py     # 独立栏目、同作寿星、日期过滤、清单与新作回归
```

它会导入全部模块、校验模板锚点与主题／版式清单、把各种畸形配置喂给设置解析器，
再用 Jinja 把四种版式 × 四套主题各渲染一遍，确认清晰度与风控相关的参数都会收敛到合法值，
并确认用户数据里的 `<script>` 无法越出页面。
Bangumi 回归覆盖跨站外链／别名、同名歧义与续作拒绝、无评分、限流冷却、取消共享请求、榜单门槛及每日推送；HTTP 测试仅连接本机临时端口，不访问第三方站点。
文档检查确认相对链接、图片和锚点有效，并核对 LLM 工具清单、返回上限及配置开关，避免说明与实现脱节。
每日回归覆盖同作寿星补齐、成功精选后去重、独立图片、配置数量、闰日、首发日期、资料关联及无评分回退；不访问生产服务器。
提 PR 前请顺手跑一下 `ruff check .` 与 `ruff format .`。

可选运行 `python tests/render_daily_previews.py`，需要另装 Playwright 及 Chromium（仅开发预览需要，不是插件的新运行依赖）。脚本使用合成数据、程序绘制封面与头像，在四套主题中检查横向溢出，并生成清单、新作、同作寿星的示例图。

`extras/` 目录放的是可选的外部组件，不会被插件加载，也不参与自检 —— 目前有一个
[Cloudflare Worker 组件](../extras/cf-worker-relay/)，可用于处理 TouchGal 的浏览器验证，不保证可用，也不参与 Bangumi 查询。

## 实现概要

- `main.py` 负责指令映射、生命周期、定时任务和工具注册；`core/` 按网络、业务、数据类型、渲染和命令分层。
- 图片使用内存与磁盘缓存，支持容量和有效期限制；渲染失败默认回退纯文本。
- Bangumi 独立处理超时、限流和缓存，相同请求合并，缓存最多 256 项；补充资料失败不阻断已有作品卡片。
- 插件卸载或重载时释放连接池、定时任务与 LLM 工具。工具实现见 `core/service/llm_tools.py`。
- 对外数据源只用于查询，请在测试中使用模拟数据或本机服务，不要高频请求第三方站点。

## 许可与素材

本项目以 **AGPL-3.0** 授权，全文见 [LICENSE](../LICENSE)。

本插件是 [PyuraMazo/astrbot_plugin_galgame_box](https://github.com/PyuraMazo/astrbot_plugin_galgame_box) 的衍生作品。
原项目同为 AGPL-3.0，其署名与版权声明依许可证要求保留。插件使用独立的包名、指令与数据目录。

原插件内置的美术素材没有沿用。本仓库的 logo、四套主题样式、图片缺失时的占位图，
以及预览图里的示例封面与截图，都是用代码现场绘制的原创素材；正文字体通过 CDN 引用，未打包进仓库。

完整的改动记录见 [CHANGELOG.md](../CHANGELOG.md)。
