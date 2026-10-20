# Practice02 —— LLM 多轮对话 + Function Call 练习

本目录是基于 Practice01（流式多轮对话）的进阶练习：在连续对话中接入 **Function Call（工具调用）**，让模型在需要时主动调用本地工具并把结果喂回，最终生成回答。

## 脚本演进

| 脚本 | 说明 |
|---|---|
| `chat.py` | 基础版：多轮连续对话 + `getDate` 工具（调用本地 `date` 命令） |
| `chat_leap.py` | 在 `chat.py` 基础上新增 `isLeapYear` 工具（纯 Python 判断闰年） |
| `chat_files.py` | 在 `chat_leap.py` 基础上新增 **10 个本地文件操作工具**（本目录核心） |

## chat_files.py 功能概览

- 流式（SSE）请求 OpenAI 兼容 `/chat/completions` 接口，打字机式输出
- 完整多轮上下文：`history` 保存全部问答，每次请求整体携带
- 工具调用循环：模型返回 `tool_calls` → 本地执行 → 结果以 `tool` 消息喂回 → 直到给出最终文本（单轮最多 `MAX_TOOL_ROUNDS = 10` 轮，防止无限循环）
- 共 **12 个工具**（2 个原有 + 10 个文件操作）

### 工具清单

| 工具名 | 功能 | 实现方式 |
|---|---|---|
| `getDate` | 获取当前日期和时间 | `subprocess` 调用本地 `date`/`time` 命令 |
| `isLeapYear` | 判断年份是否为闰年 | 纯 Python（格里高利历规则） |
| `getCurrentDirectory` | 获取当前工作目录 | `os.getcwd` |
| `listDirectory` | 列出目录下所有文件和子目录（仅一层，区分 `[目录]`/`[文件]`） | `os.scandir` |
| `getFileType` | 按扩展名推断文件类型（MIME、类别、大小） | `mimetypes` + 内置扩展名补充映射 |
| `readFile` | 读取纯文本文件内容（拒绝二进制文件） | 纯 Python，编码自动探测 |
| `createFile` | 按目录 + 文件名创建新文件（自动创建缺失目录；已存在不覆盖） | `os.makedirs` + UTF-8 写入 |
| `editFile` | 编辑纯文本文件：传 `old_text` 则替换全部匹配处，否则整体覆盖；保持原编码写回 | 纯 Python |
| `createDirectory` | 创建新目录（支持递归创建多级父目录） | `os.makedirs` |
| `deleteDirectory` | 删除目录及其全部内容（**支持非空目录，不可恢复**） | `shutil.rmtree` |
| `deleteFile` | 删除文件（误传目录会提示改用 `deleteDirectory`） | `os.remove` |
| `renameItem` | 重命名/移动文件或目录（目标已存在不覆盖） | `os.rename` |

### 关键设计点

- **纯文本保护**：`readFile` / `editFile` 通过公共助手 `_read_text_file()` 读取——先做空字节检测排除二进制文件，再依次按 UTF-8-BOM / UTF-8 / GBK 探测解码；编辑写回时保持原编码。
- **上下文保护**：单文件最多读取 `MAX_READ_BYTES = 1MB`，超出截断并提示。
- **控制台防刷屏**：工具结果超过 300 字符时控制台只显示摘要，发给模型的仍是完整结果。
- **防覆盖**：`createFile`、`renameItem` 遇到已存在的目标一律报错不覆盖；删除类操作在工具描述中明确要求模型谨慎调用。

## 用法

```bash
pip install requests

python chat_files.py              # 进入连续对话，输入问题后回车发送
python chat_files.py "你的问题"    # 先发送该问题，之后继续交互输入
# 按 Ctrl+C 退出
```

示例问法：

- “当前工作目录是什么？”
- “列出 D:\temp 下的文件和子目录”
- “在 D:\temp 下创建一个 hello.txt，内容是你好”
- “把 hello.txt 里的‘你好’替换成‘你好，世界’”
- “demo.txt 是什么类型的文件？”
- “删除 D:\temp\hello.txt”

## 配置

与脚本同目录的 `config.ini`：

```ini
[llm]
base_url = https://your-llm-endpoint/v1
api_key = your-api-key
model = your-model-name
timeout = 60
```

## 架构图

`chat_files.py` 的代码架构图（Mermaid 格式）：[chat_files_architecture.mmd](chat_files_architecture.mmd)

架构分为五层：

1. **配置层**：`config.ini` → `load_config()`
2. **主控层**：`main()` 交互循环、`history` 多轮上下文、工具调用轮次控制与异常处理
3. **LLM 通信层**：`chat_stream()`（SSE 流式请求）→ `stream_one_round()`（打字机输出 + `tool_calls` 按 index 拼接）
4. **工具调度层**：`run_tools()` 解析参数 JSON 并经 `TOOL_IMPLS` 分发
5. **工具实现层**：12 个工具函数，文件操作工具统一通过 `os` / `shutil` / `mimetypes` 与本地文件系统交互

## 目录结构

```
Practice02/
├── chat.py                     # 基础版：对话 + getDate
├── chat_leap.py                # 新增 isLeapYear
├── chat_files.py               # 新增 10 个本地文件操作工具
├── chat_files_architecture.mmd # chat_files.py 架构图（Mermaid）
├── config.ini                  # LLM 接口配置（需自行填写）
└── README.md
```
