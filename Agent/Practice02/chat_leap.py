# -*- coding: utf-8 -*-
"""
LLM 多轮连续对话 + Function Call（工具调用）练习

在 Practice02/chat.py 基础上：
    1. 保留 getDate 工具（调用本地 date 命令）
    2. 新增 isLeapYear 工具：判断输入的年份是否为闰年
    3. isLeapYear 用纯 Python 代码计算（格里高利历规则：
       能被 4 整除但不能被 100 整除，或者能被 400 整除）

用法：
    python chat_leap.py              # 进入连续对话，输入问题后回车发送
    python chat_leap.py "你的问题"    # 先发送该问题，之后继续交互输入

    试试问它："2000年是闰年吗？"、"1900年是不是闰年？"、"现在几点了？"

退出：
    按 Ctrl+C 强制终止程序

依赖：
    pip install requests
"""

import sys
import os
import json
import subprocess
import configparser

import requests

# 配置文件与当前脚本放在同一目录
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 60

# 单轮对话中允许的最大工具调用轮次（防止模型无限循环调用工具）
MAX_TOOL_ROUNDS = 5


# ===== Function Call 工具定义（OpenAI 兼容格式） =====
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间。当用户询问现在的时间、日期、星期几等问题时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断输入的年份是否为闰年。当用户询问某一年是不是闰年时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    },
                },
                "required": ["year"],
            },
        },
    },
]


class LLMStreamError(Exception):
    """流式响应内部返回错误对象时抛出（用于避免把半截回复存入聊天记录）"""
    pass


# ===== 工具实现 =====

def tool_get_date(args):
    """调用本地 date 命令获取当前日期和时间（不使用 Python 的 datetime）"""
    try:
        if os.name == "nt":
            # Windows：直接运行 date 会进入交互模式，必须加 /t 参数；
            # date 和 time 是 cmd 内部命令，需要 shell=True
            date_part = subprocess.run(
                "date /t", shell=True, capture_output=True, text=True, timeout=10)
            time_part = subprocess.run(
                "time /t", shell=True, capture_output=True, text=True, timeout=10)
            output = (date_part.stdout.strip() + " " + time_part.stdout.strip()).strip()
        else:
            # Linux / macOS：date 命令直接输出日期和时间
            result = subprocess.run(
                ["date"], capture_output=True, text=True, timeout=10)
            output = result.stdout.strip()
        return output if output else "date 命令没有返回内容"
    except Exception as e:
        return "调用 date 命令失败：%s" % e


def tool_is_leap_year(args):
    """判断年份是否为闰年（纯 Python 代码计算，不调用任何系统命令）

    格里高利历闰年规则：
        - 能被 4 整除但不能被 100 整除的年份是闰年（如 2024）
        - 能被 400 整除的年份也是闰年（如 2000）
        - 其余年份不是闰年（如 1900、2023）
    """
    year = args.get("year")
    if year is None:
        return "缺少必需参数 year"

    try:
        year = int(year)
    except (TypeError, ValueError):
        return "参数 year 必须是整数，实际收到：%r" % (year,)

    is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    return "%d 年%s闰年" % (year, "是" if is_leap else "不是")


# 工具名 -> 实现函数 的映射（实现函数统一接收解析后的参数字典）
TOOL_IMPLS = {
    "getDate": tool_get_date,
    "isLeapYear": tool_is_leap_year,
}


def load_config(path=CONFIG_FILE):
    """读取 config.ini，返回 (base_url, api_key, model, timeout)"""
    if not os.path.exists(path):
        raise FileNotFoundError("找不到配置文件：%s" % path)

    parser = configparser.ConfigParser()
    # 保留 key 的大小写（默认会转小写，这里保持原样更直观）
    parser.optionxform = str
    parser.read(path, encoding="utf-8")

    if not parser.has_section("llm"):
        raise KeyError("配置文件里缺少 [llm] 段")

    def get(key, default=None):
        value = parser.get("llm", key, fallback=default)
        return value.strip() if isinstance(value, str) else value

    base_url = get("base_url")
    api_key = get("api_key")
    model = get("model")
    timeout = get("timeout", str(DEFAULT_TIMEOUT))

    if not base_url:
        raise KeyError("[llm] 缺少 base_url 配置")
    if not api_key:
        raise KeyError("[llm] 缺少 api_key 配置")
    if not model:
        raise KeyError("[llm] 缺少 model 配置")

    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return base_url.rstrip("/"), api_key, model, timeout


def chat_stream(messages, base_url, api_key, model, timeout=DEFAULT_TIMEOUT):
    """向 LLM 接口发送【流式】请求（带 tools），逐段 yield 模型返回的 delta 对象

    delta 中可能包含：
        - content：文本增量
        - tool_calls：工具调用增量（按 index 分片，需要客户端累积拼接）
    """
    url = base_url + "/chat/completions"

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        # 完整的历史对话 + 当前用户消息
        "messages": messages,
        # 关键：开启流式
        "stream": True,
        # 声明可用工具，让模型在需要时发起 function call
        "tools": TOOLS,
    }

    # stream=True：不一次性读完响应，而是边收边读
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout, stream=True)
    # 非 2xx 直接抛 HTTPError
    resp.raise_for_status()

    # 逐行读取 SSE 分片：每行形如 "data: {...}"，以 "data: [DONE]" 结束
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line.startswith("data:"):
            continue

        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break

        try:
            obj = json.loads(data)
        except ValueError:
            continue  # 跳过无法解析的行

        # 服务端可能在流中返回错误对象
        if isinstance(obj, dict) and obj.get("error"):
            err = obj["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise LLMStreamError(msg)

        choices = obj.get("choices") or []
        if not choices:
            continue

        delta = choices[0].get("delta") or {}
        if delta:
            yield delta


def stream_one_round(messages, base_url, api_key, model, timeout):
    """执行一轮流式请求：边收边打印文本增量（打字机效果）

    返回 (完整文本, 合并后的 tool_calls 列表)。
    tool_calls 中每项形如 {"id": ..., "name": ..., "arguments": ...}
    """
    content_parts = []
    # 流式中 tool_calls 按 index 分片到达，这里按 index 累积拼接
    tool_slots = {}

    for delta in chat_stream(messages, base_url, api_key, model, timeout):
        # 1) 文本增量：打字机输出
        content = delta.get("content")
        if content:
            print(content, end="", flush=True)  # flush 保证逐段立即输出
            content_parts.append(content)

        # 2) 工具调用增量：按 index 累积 id / name / arguments
        for tc in delta.get("tool_calls") or []:
            index = tc.get("index") or 0
            slot = tool_slots.setdefault(
                index, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]

    tool_calls = [tool_slots[i] for i in sorted(tool_slots)]
    return "".join(content_parts), tool_calls


def run_tools(tool_calls):
    """执行模型请求的工具调用，返回对应的 tool 消息列表"""
    tool_messages = []
    for tc in tool_calls:
        name = tc["name"]
        impl = TOOL_IMPLS.get(name)
        if impl is None:
            result = "未知工具：%s" % name
        else:
            # 模型传来的 arguments 是 JSON 字符串，先解析成字典再交给工具实现
            raw_args = (tc.get("arguments") or "").strip()
            try:
                args = json.loads(raw_args) if raw_args else {}
            except ValueError:
                args = None
            if not isinstance(args, dict):
                result = "工具参数不是合法的 JSON 对象：%s" % raw_args
            else:
                result = impl(args)
        print("[工具调用] %s(%s) -> %s" % (name, tc.get("arguments") or "", result))
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": str(result),
        })
    return tool_messages


def main():
    # 1) 读取配置
    try:
        base_url, api_key, model, timeout = load_config()
    except (FileNotFoundError, KeyError, configparser.Error) as e:
        print("[配置错误] %s" % e)
        return 1

    # 命令行参数作为“第一条”问题，之后改为交互输入
    argv_question = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None

    # 聊天记录：保存全部历史对话，每次请求都完整带上
    history = []

    print("已进入连续对话模式（支持 getDate / isLeapYear 工具调用），输入问题后回车发送；按 Ctrl+C 退出。\n")

    # 2) 无限循环：持续接收输入 → 发起流式请求（可能带工具调用）→ 打印结果
    try:
        while True:
            # 2.1) 获取用户输入
            if argv_question is not None:
                question = argv_question
                argv_question = None  # 只消费一次
                print("你: %s" % question)
            else:
                try:
                    question = input("你: ").strip()
                except EOFError:
                    print("\n[输入已结束]")
                    break

            # 空输入：不退出，直接进入下一轮
            if not question:
                continue

            # 2.2) 组装消息：历史全部对话 + 当前用户消息
            messages = history + [{"role": "user", "content": question}]

            # 2.3) 流式请求 + 工具调用循环：
            #      模型返回 tool_calls → 执行工具 → 把结果喂回模型 → 直到模型给出最终文本
            print("===== 模型回复 =====")
            answer = ""
            try:
                for _ in range(MAX_TOOL_ROUNDS):
                    round_text, tool_calls = stream_one_round(
                        messages, base_url, api_key, model, timeout)
                    if round_text:
                        print()  # 文本输出后换行
                    answer += round_text

                    if not tool_calls:
                        break  # 模型没有调用工具，本轮结束

                    # 把模型的助手消息（含 tool_calls）追加进请求消息
                    messages.append({
                        "role": "assistant",
                        "content": round_text if round_text else None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in tool_calls
                        ],
                    })
                    # 执行工具并把结果以 tool 消息喂回模型
                    messages.extend(run_tools(tool_calls))
                else:
                    print("[提示] 工具调用轮次过多，已停止。")
            except requests.exceptions.Timeout:
                print("\n[请求超时] 请检查网络，或调大 config.ini 中的 timeout。")
            except requests.exceptions.ConnectionError:
                print("\n[连接失败] 无法访问接口地址，请检查 base_url 与网络。")
            except requests.exceptions.HTTPError as e:
                # 尽量把服务端返回的错误信息带出来
                body = ""
                if e.response is not None:
                    body = e.response.text[:500]
                print("\n[接口返回错误] %s %s" % (e, body))
            except LLMStreamError as e:
                print("\n[接口返回错误] %s" % e)
            except (KeyError, IndexError, ValueError):
                print("\n[解析失败] 接口返回格式不符合预期，请检查 base_url / model 是否正确。")
            except requests.exceptions.RequestException as e:
                print("\n[请求异常] %s" % e)
            else:
                # 2.4) 本轮正常完成后，把问答追加进聊天记录
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": answer})

            print()  # 每轮之间空一行
    except KeyboardInterrupt:
        # 唯一退出方式：Ctrl+C
        print("\n[已退出] 再见！")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
