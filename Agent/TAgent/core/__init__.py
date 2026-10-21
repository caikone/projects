# -*- coding: utf-8 -*-
"""TAgent 核心层（Core）：与界面完全无关的业务内核。

分层（依赖方向只能由外向内：界面 → 应用服务 → 领域 → 无）：

    config/          配置模型与加载（config.ini + 环境变量）
    domain/          领域模型与事件（消息、工具调用、文件值对象、事件）
    ports/           抽象端口（LLM / 工具 / 前端）——依赖倒置的关键
    services/        领域与应用服务（文件系统、历法、会话、Agent 编排）
    tools/           内置工具的适配器（把服务暴露为 LLM 工具）
    infrastructure/  外部世界适配器（HTTP/SSE 客户端、Mock 客户端）

硬约束（新增代码请遵守）：
    1. core 内不得 import 任何 UI 框架（textual / fastapi / PySide 等）；
    2. core 内不得出现 print / input；对外通信一律用事件；
    3. 第三方 SDK 只允许出现在 infrastructure/ 下。
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
