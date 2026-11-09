# -*- coding: utf-8 -*-
"""配置数据模型：配置一旦加载完成，全系统只传递这两个不可变对象。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class LLMSettings:
    """LLM 接口相关配置。"""

    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0
    temperature: float | None = None
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("base_url 不能为空")
        if not self.api_key:
            raise ValueError("api_key 不能为空")
        if not self.model:
            raise ValueError("model 不能为空")

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def masked_api_key(self) -> str:
        """脱敏后的密钥，供 UI 安全展示。"""
        key = self.api_key
        if len(key) <= 8:
            return "*" * len(key)
        return "%s***%s" % (key[:4], key[-4:])


@dataclass(frozen=True)
class SearchSettings:
    """联网搜索相关配置（百度千帆智能搜索）。

    全部字段均可为空/默认值：缺少密钥时不阻止应用启动，
    而是在 searchwdb 工具被调用时返回清晰的错误信息。
    """

    #: 百度千帆智能搜索 API 密钥（config.ini [search] 段）
    baidu_qianfan_api_key: str = ""
    #: 智能搜索接口地址（OpenAI 兼容，代码自动拼接 /chat/completions）
    base_url: str = "https://qianfan.baidubce.com/v2/ai_search"
    #: 搜索请求超时时间（秒）
    timeout: float = 30.0

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def configured(self) -> bool:
        """是否已配置好可用的搜索密钥。"""
        return bool(self.baidu_qianfan_api_key)


@dataclass(frozen=True)
class UISettings:
    """界面表现相关配置（`[ui]` 段）。

    界面里那些「看起来天生该写死在代码里」的数值全部提到这里：
    终端尺寸、性能手感因机器而异，用户应该能自己调。
    """

    #: 工作线程事件回流到 UI 线程的间隔（毫秒）。越小越顺滑，越大越省 CPU
    event_drain_interval_ms: int = 33
    #: 流式输出的重绘节流间隔（毫秒）
    stream_flush_interval_ms: int = 50
    #: 「思考中」转圈动画的帧间隔（毫秒）
    spinner_interval_ms: int = 83
    #: 工具面板里单个结果的预览上限（字符）
    tool_preview_chars: int = 400
    #: 左侧边栏宽度（列）
    sidebar_width: int = 30
    #: 右侧工具活动面板宽度（列）
    tool_panel_width: int = 42
    #: 斜杠命令补全面板最多显示几行
    command_panel_rows: int = 8
    #: 输入框高度（行，含上边框）
    input_height: int = 7
    #: 边栏里每台 MCP 服务器最多列出多少个工具（其余折叠成统计）
    sidebar_tool_limit: int = 12

    @property
    def event_drain_interval(self) -> float:
        """换算成秒（Textual 定时器的单位）。"""
        return max(1, self.event_drain_interval_ms) / 1000.0

    @property
    def stream_flush_interval(self) -> float:
        return max(1, self.stream_flush_interval_ms) / 1000.0

    @property
    def spinner_interval(self) -> float:
        return max(1, self.spinner_interval_ms) / 1000.0


@dataclass(frozen=True)
class SessionSettings:
    """会话持久化配置（`[sessions]` 段）。"""

    #: 会话目录；留空 = 「启动时工作目录/.agents/sessions」
    directory: str = ""
    #: 退出 / 新建会话时是否自动保存
    autosave: bool = True
    #: /resume 列表最多展示多少条
    max_listed: int = 50


@dataclass(frozen=True)
class WebSettings:
    """Web 界面配置（`[web]` 段）。"""

    #: 监听地址：默认只对本机开放；想让手机/同事访问再改成 0.0.0.0
    host: str = "127.0.0.1"
    #: 端口
    port: int = 8760
    #: 启动后自动用默认浏览器打开
    open_browser: bool = True
    #: 前端构建产物目录；留空 = 「项目根/frontend/dist」
    assets_dir: str = ""
    #: SSE 心跳间隔（秒）：长时间无输出时保活，防止中间代理掐断
    heartbeat_seconds: float = 15.0


@dataclass(frozen=True)
class AgentsDocSettings:
    """AGENTS.md 项目约定注入配置（`[agent_docs]` 段）。"""

    enabled: bool = True
    #: 文件名（相对启动目录）
    filename: str = "AGENTS.md"
    #: 注入内容的最大字节数，超出截断
    max_bytes: int = 32768


@dataclass(frozen=True)
class AgentSettings:
    """Agent 运行时配置（LLM 配置 + 会话/工具策略）。"""

    llm: LLMSettings
    #: 联网搜索配置（可缺省，缺省时 searchwdb 工具调用会返回错误提示）
    search: SearchSettings = field(default_factory=SearchSettings)
    #: 界面配置（`[ui]` 段）
    ui: UISettings = field(default_factory=UISettings)
    #: 会话持久化配置（`[sessions]` 段）
    sessions: SessionSettings = field(default_factory=SessionSettings)
    #: Web 界面配置（`[web]` 段）
    web: WebSettings = field(default_factory=WebSettings)
    #: AGENTS.md 注入配置（`[agent_docs]` 段）
    agents_doc: AgentsDocSettings = field(default_factory=AgentsDocSettings)

    #: 单轮对话中允许的最大「模型请求 → 工具执行」循环次数（防止无限循环）
    max_tool_rounds: int = 10
    #: 读取纯文本文件的最大字节数，超出截断
    max_read_bytes: int = 1024 * 1024
    #: 工具结果在 UI 上的最大展示长度（发给模型的仍是完整结果）
    max_tool_display_len: int = 300
    #: 系统提示词
    system_prompt: str = (
        "你是一个可以操作本地文件的智能助手。"
        "需要获取信息或改变文件系统状态时，优先调用合适的工具，"
        "再根据工具返回的结果用中文回答用户。"
        "回答原则：当用户只是想要概况、总结或询问问题时，用简洁的语言概括；"
        "但当用户明确要求「原文」「全文」「完整内容」「详细内容」或"
        "「把网页内容原样贴出来」时，必须把工具（如 fetchWeb）返回的"
        "Markdown 正文尽可能完整、逐字地复述出来，不要自己概括、压缩或改写。"
    )
    #: 是否把系统提示词插入会话
    enable_system_prompt: bool = True
    #: 会话历史最多保留的消息条数（0 表示不限制）
    history_limit: int = 0
    #: Skill 根目录；留空则使用「启动时工作目录/.agents/skills」
    skills_dir: str = ""
    #: 是否启用 MCP（外部工具服务器）
    mcp_enabled: bool = True
    #: MCP 配置文件路径；留空则使用「启动时工作目录/.mcp.json」
    mcp_config: str = ""
    #: 单次 MCP 工具调用超时（秒）
    mcp_timeout: float = 60.0
    #: 单个 MCP Server 的连接与握手超时（秒）
    mcp_connect_timeout: float = 15.0
    #: 单次 MCP 工具结果保留的最大字符数（超出保留头 70% + 尾 30%）
    mcp_max_result_chars: int = 20000
    #: 注入模型上下文时，单个 MCP 工具描述的最大字符数
    mcp_tool_description_chars: int = 1500
    #: 模型上下文窗口大小（token）。0 = 未知，按默认 128k 处理并在 UI 标注「默认」
    context_window: int = 0
    #: 上下文占用达到窗口的该比例时提示 /compact 并暂停普通输入
    compact_threshold: float = 0.8
    #: 上下文归档目录；留空则使用「启动时工作目录/.agents/context」
    context_dir: str = ""
    #: 压缩摘要的目标 token 数（约数，超出 2 倍字符数会本地截断）
    context_summary_tokens: int = 5000
    #: recallContext 单次返回内容的最大字符数
    context_recall_max_chars: int = 8000
    #: 单条命令的执行超时（秒）——超时后把已产生的输出交给模型
    cli_timeout: float = 30.0
    #: 命令 stdout / stderr 保留的最大字符数
    cli_max_stdout_chars: int = 8000
    cli_max_stderr_chars: int = 4000
    #: 网页抓取：整体超时 / 连接超时（秒）与 HTML 最大字节数
    webfetch_timeout: float = 25.0
    webfetch_connect_timeout: float = 10.0
    webfetch_max_html_bytes: int = 8 * 1024 * 1024

    def with_overrides(self, **changes: object) -> "AgentSettings":
        """返回一份改过若干字段的新配置（保持不可变语义）。"""
        return replace(self, **changes)


# ----------------------------------------------------------------------
# 环境变量覆盖：方便 CI / 容器场景不落盘密钥
# ----------------------------------------------------------------------
ENV_PREFIX = "TAGENT_"

_ENV_MAP = {
    "BASE_URL": "base_url",
    "API_KEY": "api_key",
    "MODEL": "model",
    "TIMEOUT": "timeout",
}


def env_overrides() -> dict[str, object]:
    """从环境变量读取配置覆盖项，键形如 `TAGENT_API_KEY`。"""
    overrides: dict[str, object] = {}
    for env_key, field_name in _ENV_MAP.items():
        raw = os.environ.get(ENV_PREFIX + env_key)
        if raw is None or not raw.strip():
            continue
        value: object = raw.strip()
        if field_name == "timeout":
            try:
                value = float(raw)
            except ValueError:
                continue
        overrides[field_name] = value
    return overrides


def default_config_paths() -> list[str]:
    """配置文件的默认搜索顺序（越靠前优先级越高）。"""
    env_path = os.environ.get(ENV_PREFIX + "CONFIG")
    paths: list[str] = []
    if env_path:
        paths.append(env_path)
    paths.append(os.path.join(os.getcwd(), "config.ini"))
    # models.py -> config/ -> core/ -> TAgent/（项目根）
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    paths.append(os.path.join(project_root, "config.ini"))
    return paths


__all__ = [
    "LLMSettings",
    "SearchSettings",
    "UISettings",
    "SessionSettings",
    "AgentsDocSettings",
    "AgentSettings",
    "env_overrides",
    "default_config_paths",
]
