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
    ├── SearchError                 联网搜索错误（缺配置 / 网络失败 / API 返回错误 / 响应格式异常）
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
# 联网搜索
# ==========================================================================
class SearchError(TAgentError):
    """联网搜索过程中的错误：缺少配置、网络失败、API 返回错误、响应格式异常等。

    工具护栏会把该异常转成 `ToolOutcome.failure`，
    让模型能看到清晰的错误说明并据此答复用户。
    """

    kind = "search"


# ==========================================================================
# 网页抓取
# ==========================================================================
class WebFetchError(TAgentError):
    """网页抓取过程中的所有错误的基类。

    `fetchWeb` 工具处理失败时会转成 `ToolOutcome.failure`，
    模型据此告知用户「抓取失败」而不是编造内容。
    """

    kind = "webfetch"


class InvalidURLError(WebFetchError):
    """URL 非法或不被支持（缺 scheme、scheme 不是 http/https 等）。"""

    def __init__(self, url: str, reason: str = "") -> None:
        message = "无法抓取 %s：URL %s" % (url, reason or "非法")
        super().__init__(message, url=url)
        self.url = url


class WebFetchConnectionError(WebFetchError):
    """底层抓取程序不可用，或目标主机不可达 / DNS 失败 / 连接被拒。"""

    def __init__(self, message: str = "无法访问目标网页，请检查 URL 与网络。") -> None:
        super().__init__(message)


class WebFetchTimeoutError(WebFetchError):
    """抓取超时。"""

    def __init__(self, timeout: float | int, url: str = "") -> None:
        message = "抓取超时（%s 秒），请检查网络或调大 [webfetch] timeout。" % timeout
        if url:
            message += " 目标：%s" % url
        super().__init__(message, timeout=timeout, url=url)
        self.timeout = timeout


class WebFetchHTTPError(WebFetchError):
    """目标服务器返回非 2xx 状态码。"""

    def __init__(self, status_code: int, url: str = "") -> None:
        message = "目标网页返回 HTTP %s" % status_code
        if url:
            message += "：%s" % url
        super().__init__(message, status_code=status_code, url=url)
        self.status_code = status_code


class WebFetchParseError(WebFetchError):
    """HTML 解析失败（trafilatura / lxml 提取过程出错）。"""

    def __init__(self, message: str = "网页 HTML 解析失败，无法提取正文。") -> None:
        super().__init__(message)


class WebFetchEmptyError(WebFetchError):
    """拿到了 HTTP 响应，但没能提取出正文（纯导航页 / JS 渲染页 / 反爬页等）。"""

    def __init__(self, url: str = "", detail: str = "") -> None:
        message = "未能从 %s 提取到正文内容" % (url or "目标网页")
        if detail:
            message += "（%s）" % detail
        message += "：页面可能需要 JavaScript 渲染，或正文过短。"
        super().__init__(message, url=url)
        self.url = url


# ==========================================================================
# Agent Skills
# ==========================================================================
class SkillError(TAgentError):
    """Skill 发现 / 加载过程中的所有错误的基类。

    与 `CliError` 同理：这些异常会被工具护栏转成 `ToolOutcome.failure`，
    让模型看到「为什么没拿到」并自行决策（换名字 / 换路径 / 向用户说明），
    而不是让整轮会话崩掉。
    """

    kind = "skill"


class SkillNotFoundError(SkillError):
    """模型请求的 Skill 名称不存在。"""

    def __init__(self, name: str, available: "list[str] | None" = None) -> None:
        message = "未找到名为「%s」的 Skill。" % name
        if available:
            message += "当前可用：%s。" % "、".join(available)
        else:
            message += "当前项目没有已发现的 Skill（检查 .agents\\skills\\ 目录）。"
        super().__init__(message, name=name, available=available or [])
        self.name = name
        self.available = list(available or [])


class SkillArgumentError(SkillError):
    """调用参数不合法（缺 name、name 为空等）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SkillAlreadyExistsError(SkillError):
    """目标 Skill 已存在（createSkill 绝不覆盖）。"""

    def __init__(self, name: str, directory: str = "", entry: str = "") -> None:
        message = "Skill「%s」已存在，本次未做任何改动，以免覆盖原有内容。" % name
        if entry:
            message += "现有入口：%s。" % entry
        elif directory:
            message += "现有目录：%s。" % directory
        message += (
            "如需修改它，请先 readFile 读取该 SKILL.md，再用 editFile 修改；"
            "确需重建请先删除该 Skill 目录，或换一个新的名称。"
        )
        super().__init__(message, name=name, directory=directory, entry=entry)
        self.name = name
        self.directory = directory
        self.entry = entry


class SkillFileNotFoundError(SkillError):
    """Skill 目录内找不到指定文件。"""

    def __init__(self, skill: str, filename: str) -> None:
        super().__init__(
            "Skill「%s」内找不到文件：%s。可只传 name 加载 SKILL.md，"
            "或先加载 SKILL.md 查看它引用了哪些附加文件。" % (skill, filename),
            skill=skill,
            filename=filename,
        )
        self.skill = skill
        self.filename = filename


class SkillAmbiguousFileError(SkillError):
    """同名文件有多个候选，需要模型指定更精确的路径。"""

    def __init__(self, skill: str, filename: str, candidates: "list[str]") -> None:
        listed = "\n".join("  - %s" % c for c in candidates)
        super().__init__(
            "Skill「%s」内有 %d 个同名文件，无法确定要读哪一个：\n%s\n"
            "请用更精确的相对路径（相对 Skill 目录）重试。"
            % (skill, len(candidates), listed),
            skill=skill,
            filename=filename,
            candidates=candidates,
        )
        self.skill = skill
        self.filename = filename
        self.candidates = list(candidates)


class SkillPathError(SkillError):
    """解析后的路径逃出了 Skill 目录（越界读取被拒绝）。"""

    def __init__(self, skill: str, path: str) -> None:
        super().__init__(
            "拒绝读取：%s 解析后不在 Skill「%s」的目录内。"
            "LoadSkill 只能读取该 Skill 目录内部的文件。" % (path, skill),
            skill=skill,
            path=path,
        )
        self.skill = skill
        self.path = path


class SkillFileError(SkillError):
    """文件存在但读不了（疑似二进制 / 无法解码 / 权限不足）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# ==========================================================================
# 本地命令行（CLI）
# ==========================================================================
class CliError(TAgentError):
    """本地命令行执行过程中的所有错误的基类。

    注意区分两类「不成功」：
        - **工具失败**（程序不存在 / 无法启动）：转成 `ToolOutcome.failure`；
        - **命令执行完了但退出码非 0**（如 `git status` 在非仓库目录）：
          这不是工具故障，退出码与 stderr 必须原样交给模型判断，见
          `services/cli.py::CommandService.render`。
    """

    kind = "cli"


class CliNotFoundError(CliError):
    """PATH 里找不到要执行的程序。"""

    def __init__(self, command: str) -> None:
        message = (
            "找不到可执行程序：%s。请确认它已安装并加入 PATH，"
            "或改用完整路径（如 C:\\Windows\\System32\\curl.exe）。" % command)
        super().__init__(message, command=command)
        self.command = command


class CliArgumentError(CliError):
    """命令名 / 参数不合法（空命令名、args 不是字符串数组等）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class CliExecutionError(CliError):
    """进程无法启动或被操作系统拒绝（权限不足、不是可执行文件等）。"""

    def __init__(self, command: str = "", detail: str = "") -> None:
        message = "无法启动程序 %s" % (command or "该命令")
        if detail:
            message += "：%s" % detail
        super().__init__(message, command=command)
        self.command = command


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
