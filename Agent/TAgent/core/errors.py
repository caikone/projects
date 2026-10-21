# -*- coding: utf-8 -*-
"""TAgent 统一异常层级。

设计原则：
    - 核心层只抛出本模块定义的异常，**永不**让 `requests` / 具体 SDK 的异常泄漏到 UI 层；
    - 基础设施适配器（infrastructure）负责把第三方异常翻译成本层异常；
    - UI 层只需 `except TAgentError` 即可优雅降级，无需感知 HTTP / JSON 细节。

层级：

    TAgentError
    ├── ConfigError                 配置缺失 / 格式错误
    ├── ToolError                   工具体系错误
    │   ├── ToolNotFoundError       模型调用了不存在的工具
    │   ├── ToolArgumentError       参数不是合法 JSON 对象 / 缺少必填项
    │   └── FileSystemError         本地文件操作失败（业务语义）
    ├── FrontendError               界面加载失败（目录不存在 / 依赖缺失 / 未实现 Frontend）
    └── LLMError                    大模型通信错误
        ├── LLMConnectionError      网络不可达
        ├── LLMTimeoutError         请求超时
        ├── LLMHTTPError            接口返回非 2xx
        ├── LLMProtocolError        响应结构与 OpenAI 兼容协议不符
        └── LLMStreamError          流内返回 error 对象
"""

from __future__ import annotations


class TAgentError(Exception):
    """TAgent 所有业务异常的基类。

    Attributes:
        message: 面向用户（可直接展示）的错误说明。
    """

    #: 供 UI 层做分类展示用的短标签
    kind: str = "error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:  # pragma: no cover - 简单委托
        return self.message or self.__class__.__name__


# ==========================================================================
# 配置
# ==========================================================================
class ConfigError(TAgentError):
    """配置文件缺失、字段缺失或格式非法。"""

    kind = "config"


# ==========================================================================
# 界面层
# ==========================================================================
class FrontendError(TAgentError):
    """界面包无法加载：目录缺失、第三方依赖未安装、或未实现 Frontend 契约。

    Attributes:
        name:   界面标识（如 ``tui``）。
        reason: 具体原因（面向开发者的原文）。
        hint:   面向用户的修复建议（可为空）。
    """

    kind = "frontend"

    def __init__(self, message: str = "", *, name: str = "",
                 reason: str = "", hint: str = "") -> None:
        super().__init__(message, name=name, reason=reason, hint=hint)
        self.name = name
        self.reason = reason
        self.hint = hint


# ==========================================================================
# 工具体系
# ==========================================================================
class ToolError(TAgentError):
    """工具注册 / 分发 / 执行过程中的错误。"""

    kind = "tool"


class ToolNotFoundError(ToolError):
    """模型请求了一个未注册的工具。"""

    def __init__(self, name: str) -> None:
        super().__init__("未知工具：%s" % name, name=name)
        self.name = name


class ToolArgumentError(ToolError):
    """工具参数无法解析为合法的对象。"""

    def __init__(self, name: str, raw_arguments: str) -> None:
        super().__init__(
            "工具 %s 的参数不是合法的 JSON 对象：%s" % (name, raw_arguments),
            name=name,
            raw_arguments=raw_arguments,
        )
        self.name = name
        self.raw_arguments = raw_arguments


class FileSystemError(ToolError):
    """本地文件操作失败（不存在 / 权限不足 / 类型不符 / 非纯文本等）。"""

    kind = "filesystem"


# ==========================================================================
# LLM 通信
# ==========================================================================
class LLMError(TAgentError):
    """与大模型接口通信过程中的所有错误的基类。"""

    kind = "llm"


class LLMConnectionError(LLMError):
    """无法连接接口地址。"""

    def __init__(self, message: str = "无法访问接口地址，请检查 base_url 与网络。") -> None:
        super().__init__(message)


class LLMTimeoutError(LLMError):
    """请求超时。"""

    def __init__(self, message: str = "请求超时，请检查网络或调大 timeout。") -> None:
        super().__init__(message)


class LLMHTTPError(LLMError):
    """接口返回非 2xx 状态码。

    Attributes:
        status_code: HTTP 状态码（未知时为 None）。
        body: 响应体截断后的文本，便于定位问题。
    """

    def __init__(self, status_code: int | None = None, body: str = "") -> None:
        message = "接口返回 HTTP %s" % (status_code if status_code is not None else "错误")
        if body:
            message += "：%s" % body
        super().__init__(message, status_code=status_code, body=body)
        self.status_code = status_code
        self.body = body


class LLMProtocolError(LLMError):
    """响应结构与 OpenAI 兼容协议不符（缺字段、JSON 解析失败等）。"""

    def __init__(self, message: str = "接口返回格式不符合预期，请检查 base_url / model 是否正确。") -> None:
        super().__init__(message)


class LLMStreamError(LLMError):
    """流式响应中途返回了 error 对象。"""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or "接口在流式响应中返回了错误对象。")
