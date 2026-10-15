<div align="center">

<img src="logo.png" width="112" alt="Gal工房 Logo">

<h1>Gal工房</h1>

<p>在聊天里查 Galgame 评分、找作品资料，发现下一部想玩的游戏。</p>

</div>

## 能做什么

- **查评分与资料**：优先查询 Bangumi 游戏，支持作品、角色和会社检索。
- **找下一部游戏**：看评分榜、随机抽取、按标签推荐，或用图片识别角色出处。
- **收藏与每日简讯**：保存个人收藏，订阅作品发售纪念日和角色生日。
- **图片卡片**：四套配色可选；图片生成失败时，默认用文字回复。
- **让 AI 帮忙查**：可选开启四个查询工具，直接用日常语言提问。见 [LLM 工具指南](docs/llm-tools.md)。

## 预览

<img src="docs/preview/bangumi_feature.jpg" alt="游戏评分与资料卡片示例">

图中作品、评分和封面均为虚构示例，仅展示卡片样式。[查看更多版式与配色](docs/previews.md)。

## 安装

在 AstrBot WebUI 的插件管理中选择「从 URL 安装」，粘贴：

```text
https://github.com/Whereis-Alice/astrbot_plugin_vn_atelier
```

安装后发送 `工房 帮助`。默认设置即可查询公开资料，无需填写 Bangumi 令牌。
环境要求和可选设置见[配置指南](docs/configuration.md)。

## 开始使用

| 想做什么 | 发送示例 |
| --- | --- |
| 查作品 | `工房 作品 CLANNAD` |
| 查 Bangumi 评分与详情 | `工房 BGM CLANNAD` |
| 查角色或会社 | `工房 角色 古河渚`、`工房 厂商 Key` |
| 看评分榜 | `工房 排行 2024 galgame` |
| 找一部游戏 | `工房 随机`、`工房 推荐 治愈 校园` |
| 用图片找出处 | 发图片并附上 `工房 出处` |
| 收藏与查看清单 | `工房 收藏 v17`、`工房 清单` |
| 看当天纪念日与生日 | `工房 简讯` |
| 开启每日推送 | `工房 推送 开启`（AstrBot 管理员） |
| 切换卡片配色 | `工房 主题 青瓷`（AstrBot 管理员） |

如果 AstrBot 设置了指令前缀，请按实际配置添加，例如 `/工房 帮助`。
更多指令和别名见[指令说明](docs/usage.md)。

## 使用前了解

- **评分分来源**：Bangumi 为 10 分制，VNDB 为 100 分制，两者不混算。每日推送只有可靠关联到条目时才展示双来源评分。
- **条目 ID 不通用**：Bangumi 数字 ID 与 VNDB ID 不同，收藏请使用 VNDB ID，例如 `v17`。
- **第三方服务可能暂时不可用**：资源查询依赖 TouchGal；其验证问题不影响直接查询 Bangumi。遇到问题见[常见问题](docs/faq.md)。
- **成人内容默认关闭**：开启前请确认群聊和平台允许，且不要将第三方内容标记视为绝对可靠的过滤保证。

## 更多说明

| 文档 | 内容 |
| --- | --- |
| [指令说明](docs/usage.md) | 全部指令、别名与使用示例 |
| [配置指南](docs/configuration.md) | 数据源、图片清晰度、网络和每日推送设置 |
| [LLM 工具指南](docs/llm-tools.md) | 开启方式、四个工具的用途与参数、调用示例和限制 |
| [常见问题](docs/faq.md) | 图片、字体、推送和 TouchGal 验证排查 |
| [数据来源与评分说明](docs/data-sources.md) | 评分含义、榜单规则与跨站关联 |
| [卡片预览](docs/previews.md) | 四种版式与四套配色 |
| [开发与自检](docs/development.md) | 本地测试、代码结构及可选组件 |
| [更新日志](CHANGELOG.md) | 改动记录 |

## 许可与来源

本项目基于 [PyuraMazo/astrbot_plugin_galgame_box](https://github.com/PyuraMazo/astrbot_plugin_galgame_box)，采用 [AGPL-3.0](LICENSE) 许可，保留原项目署名与版权声明。
本仓库的 Logo、主题和预览示例素材由代码绘制，未沿用原插件内置美术素材。

反馈问题请[提交 Issue](https://github.com/Whereis-Alice/astrbot_plugin_vn_atelier/issues)，附上相关的 `[Gal工房]` 日志，并隐藏令牌、Cookie 和访问口令。
