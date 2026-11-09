# -*- coding: utf-8 -*-
"""配置模板与「首次运行自动生成 config.ini」。

为什么需要这个模块：
    新用户克隆仓库后直接 `python run.py`，如果因为缺少 config.ini 就报错退出，
    体验非常差（尤其对不熟悉 ini 的人）。这里改为：**先生成一份可读的完整模板**
    （带 mock 值），让程序能启动并弹出提示，用户按提示填好后重启即可。

生成的模板只填 mock 值，绝不写任何真实密钥；
`[llm]` 里的地址指向本地占位端口，调用会失败——这正是提示用户「必须修改」的原因。
"""

from __future__ import annotations

import os

#: 自动生成的配置里使用的占位值（故意不可用，避免用户误以为已经配好）
MOCK_BASE_URL = "http://127.0.0.1:8000/v1"
MOCK_API_KEY = "mock-api-key"
MOCK_MODEL = "mock-model"

#: 配置文件名
CONFIG_FILENAME = "config.ini"

CONFIG_TEMPLATE = """\
; ======================================================================
; TAgent 配置文件（由 TAgent 首次运行时自动生成）
; ----------------------------------------------------------------------
; 当前 [llm] 段是 **占位（mock）信息**，直接运行会连接失败。
; 请把 base_url / api_key / model 改成你自己的服务，然后**重新启动** TAgent。
; 本文件不会提交到版本库（见 .gitignore），可以放心填写密钥。
; ======================================================================

[llm]
; OpenAI 兼容接口的 base_url（程序会自动拼接 /chat/completions）
; 例：https://api.openai.com/v1
; 例：https://dashscope.aliyuncs.com/compatible-mode/v1
; 例：https://api.deepseek.com
base_url = {base_url}

; API 密钥（把 mock-api-key 换成真实密钥）
api_key = {api_key}

; 模型名称
model = {model}

; 请求超时时间（秒）
timeout = 60

; 采样温度（可选，留空表示用服务端默认值）
; temperature = 0.3

; 单次回复最大 token（可选；长输出场景建议显式给足）
max_tokens = 8192


; ===== Agent 运行策略 =====
[agent]
; 单轮对话中「模型请求 → 工具执行」的最大循环次数
max_tool_rounds = 10

; 读取单个文件的最大字节数（超出自动截断）
max_read_bytes = 1048576

; 工具结果在界面上的最大展示长度（发给模型的仍是完整结果）
max_tool_display_len = 300

; 会话历史最多保留多少条消息（0 = 不限制）
history_limit = 0

; 是否注入系统提示词
enable_system_prompt = true

; 自定义系统提示词（可选；多行请用缩进续行）
; system_prompt = 你是一个可以操作本地文件的智能助手……

; Skill 根目录（可选）。留空 = 启动目录/.agents/skills
; skills_dir =


; ===== 界面表现 =====
[ui]
; 后台事件回流间隔（毫秒）：越小越顺滑，越大越省 CPU
event_drain_interval_ms = 33

; 流式输出重绘间隔（毫秒）
stream_flush_interval_ms = 50

; 「思考中」转圈动画的帧间隔（毫秒）
spinner_interval_ms = 83

; 工具面板里单条结果的预览字符数
tool_preview_chars = 400

; 左侧边栏（MCP 服务器与工具）宽度，单位「列」
sidebar_width = 30

; 右侧工具活动面板宽度
tool_panel_width = 42

; 斜杠命令补全面板最多显示几行
command_panel_rows = 8

; 输入框高度（行，含上边框）
input_height = 7

; 边栏里每台 MCP 服务器最多列出多少个工具（其余折叠成统计）
sidebar_tool_limit = 12


; ===== Web 界面 =====
[web]

; 监听地址：127.0.0.1 只允许本机访问；要让手机/同事访问改成 0.0.0.0
host = 127.0.0.1

; 端口
port = 8760

; 启动后自动打开默认浏览器（true / false）
open_browser = true

; 前端构建产物目录；留空 = 项目根的 frontend/dist
; assets_dir =

; SSE 心跳间隔（秒）：长时间没有输出时保活，防止代理掐断连接
heartbeat_seconds = 15


; ===== 会话持久化 =====
[sessions]
; 会话目录（可选）。留空 = 启动目录/.agents/sessions
; dir =

; 退出程序 / 新建会话时自动保存当前会话
autosave = true

; /resume 列表最多展示多少条历史会话
max_listed = 50


; ===== AGENTS.md 项目约定 =====
[agent_docs]
; 是否把启动目录下的 AGENTS.md 注入到每个新会话（只注入一次，全程复用）
enabled = true

; 文件名
filename = AGENTS.md

; 注入内容的最大字节数（超出截断，避免一次吃掉过多上下文）
max_bytes = 32768


; ===== MCP（外部工具服务器）=====
[mcp]
; 是否启用 MCP；关闭后不会读取 .mcp.json
enabled = true

; MCP 配置文件路径（可选）。留空 = 启动目录/.mcp.json
; config =

; 单次 MCP 工具调用超时（秒）
timeout = 60

; 单个 MCP Server 的连接与握手超时（秒）；连不上只会跳过该服务器
connect_timeout = 15

; 单次 MCP 工具结果保留的最大字符数
max_result_chars = 20000

; 注入模型上下文时单个工具描述的最大字符数
tool_description_chars = 1500


; ===== 上下文窗口 / 压缩 / 检索 =====
[context]
; 模型上下文窗口大小（token）。0 = 未知，按默认 128k 处理并标注「默认」
; 常见值：deepseek-chat 128000 / gpt-4o 128000 / qwen-max 32768
window = 0

; 上下文占用达到窗口的该比例时提示并暂停普通输入
threshold = 0.8

; 归档目录（可选）。留空 = 启动目录/.agents/context
; dir =

; 压缩摘要的目标 token 数
summary_tokens = 5000

; recallContext 单次返回内容的最大字符数
recall_max_chars = 8000


; ===== 本地命令执行 =====
[cli]
; 单条命令的超时（秒）；超时会把已产生的输出交给模型
timeout = 30

; stdout / stderr 保留的最大字符数
max_stdout_chars = 8000
max_stderr_chars = 4000


; ===== 网页抓取 =====
[webfetch]
; 整体超时 / 连接超时（秒）
timeout = 25
connect_timeout = 10

; 单个页面 HTML 的最大字节数（防止超大页面拖垮解析）
max_html_bytes = 8388608


; ===== 联网搜索（可选）=====
[search]
; 百度千帆智能搜索（生成版）API 密钥；不填则该工具调用时返回错误提示
baidu_qianfan_api_key =

; 搜索接口地址（默认即千帆智能搜索 v2）
; base_url = https://qianfan.baidubce.com/v2/ai_search

; 搜索请求超时（秒）
timeout = 30
"""


def render_template(base_url: str = MOCK_BASE_URL,
                    api_key: str = MOCK_API_KEY,
                    model: str = MOCK_MODEL) -> str:
    """渲染配置模板全文（便于测试与将来做交互式向导）。"""
    return CONFIG_TEMPLATE.format(base_url=base_url, api_key=api_key, model=model)


def ensure_local_config(directory: str | None = None) -> tuple[str, bool]:
    """确保「启动目录」下存在 config.ini；不存在则写入模板。

    Args:
        directory: 目标目录，默认当前工作目录。

    Returns:
        (配置路径, 是否为本次新建)

    Raises:
        OSError: 目录不可写（调用方应转成用户可读的提示）。
    """
    base = os.path.abspath(directory or os.getcwd())
    path = os.path.join(base, CONFIG_FILENAME)
    if os.path.isfile(path):
        return path, False

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_template())
        handle.flush()
        os.fsync(handle.fileno())
    return path, True


__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_TEMPLATE",
    "MOCK_API_KEY",
    "MOCK_BASE_URL",
    "MOCK_MODEL",
    "ensure_local_config",
    "render_template",
]
