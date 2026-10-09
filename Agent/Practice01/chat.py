# -*- coding: utf-8 -*-
"""
LLM 多轮连续对话（流式 Stream 输出，打字机效果，带上下文记忆）

用法：
    python chat.py              # 进入连续对话，输入问题后回车发送
    python chat.py "你的问题"    # 先发送该问题，之后继续交互输入

退出：
    按 Ctrl+C 强制终止程序

依赖：
    pip install requests
"""

import sys
import os
import json
import configparser

import requests

# 配置文件与当前脚本放在同一目录
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 60


class LLMStreamError(Exception):
    """流式响应内部返回错误对象时抛出（用于避免把半截回复存入聊天记录）"""
    pass


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
    """向 LLM 接口发送【流式】请求，逐段 yield 模型返回的文本增量

    messages: 完整的对话消息列表，形如
              [{"role": "user", "content": "..."},
               {"role": "assistant", "content": "..."}, ...]
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
        content = delta.get("content")
        if content:
            yield content


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

    print("已进入连续对话模式，输入问题后回车发送；按 Ctrl+C 退出。\n")

    # 2) 无限循环：持续接收输入 → 发起流式请求 → 打印结果
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

            # 2.3) 发送流式请求，边收边打印（打字机效果）
            print("===== 模型回复 =====")
            answer_parts = []
            try:
                for chunk in chat_stream(messages, base_url, api_key, model, timeout):
                    print(chunk, end="", flush=True)  # flush 保证逐段立即输出
                    answer_parts.append(chunk)
                print()  # 结尾换行
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
                # 2.4) 模型回复完成后，把本轮问答追加进聊天记录
                history.append({"role": "user", "content": question})
                history.append({"role": "assistant", "content": "".join(answer_parts)})

            print()  # 每轮之间空一行
    except KeyboardInterrupt:
        # 唯一退出方式：Ctrl+C
        print("\n[已退出] 再见！")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
