"""视觉小说工房（astrbot_plugin_vn_atelier）核心实现包。

分层约定：

* core.types    —— 纯数据：枚举、常量、pydantic 模型、异常
* core.utils    —— 无状态工具：文件、图片、HTML 解析、文案拼装
* core.network  —— 外部 API 客户端（VNDB / TouchGal / AnimeTrace）
* core.service  —— 有状态服务：缓存、渲染、冷却、订阅、LLM 工具
* core.command  —— 指令处理器，每个文件对应一条子指令
* core.settings —— 配置解析与校验
* core.runtime  —— 依赖装配容器，取代全局可变状态
"""
