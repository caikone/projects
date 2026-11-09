# -*- coding: utf-8 -*-
"""一次性演示脚本：跑一条真实反诈对话，把事件流导出成报告可引用的记录。

用法（在 TAgent 目录下）：
    python scripts/anti_fraud_demo.py "我接到电话说涉嫌洗钱，要转安全账户"
    python scripts/anti_fraud_demo.py --out demo-02-investment.md "导师带单稳赚..."

输出默认写到 docs/anti-fraud/demo-transcript.md
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.container import AppContainer            # noqa: E402
from core.domain.events import (                    # noqa: E402
    AssistantMessageCompleted, AssistantTextDelta, RoundFinished,
    ToolCallCompleted, ToolCallStarted, TurnCompleted,
)

DEFAULT_PROMPT = (
    "我接到一个电话，对方说是市公安局的，说我名下银行卡涉嫌一起洗钱案，"
    "要我配合调查，把卡里的 8 万块钱转到他们提供的「安全账户」验资，"
    "还叮嘱我这事不能告诉家里人。我刚把卡号报给他了，现在有点慌，要不要转？"
)


def main() -> int:
    argv = sys.argv[1:]
    out_name = "demo-transcript.md"
    if "--out" in argv:
        index = argv.index("--out")
        out_name = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]

    prompt = " ".join(argv).strip() or DEFAULT_PROMPT
    container = AppContainer.build(defer_mcp=False)
    ctx = container.build_context(initial_prompt=None)
    session = ctx.new_session()

    lines: list[str] = []
    started = time.time()
    rounds = 0
    tool_calls: list[str] = []
    answer: list[str] = []      # 最终回答全文
    buffer: list[str] = []      # 当前这条助手消息的增量（用于落盘）

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit("# 反诈助手真实对话记录")
    emit()
    emit("> 由 `scripts/anti_fraud_demo.py` 自动生成，事件流完整保留，便于核对。")
    emit()
    emit("**用户输入**")
    emit()
    emit("```text")
    emit(prompt)
    emit("```")
    emit()
    emit("**助手过程与输出**")
    emit()
    emit("```text")

    for event in ctx.agent.stream_turn(session, prompt):
        if isinstance(event, RoundFinished):
            rounds += 1
        elif isinstance(event, ToolCallStarted):
            tool_calls.append(event.name)
            emit("[工具] %s(%s)" % (event.name, _brief(event.arguments)))
        elif isinstance(event, ToolCallCompleted):
            result = event.result
            if result is not None:
                flag = "" if result.ok else " [失败]"
                emit("       ← %s%s" % (flag, _one_line(result.content, 120)))
        elif isinstance(event, AssistantTextDelta):
            buffer.append(event.text)
            print(event.text, end="", flush=True)
        elif isinstance(event, AssistantMessageCompleted):
            text = event.content or "".join(buffer)
            buffer.clear()
            answer.append(text)
            if text.strip():
                emit(text)
                emit()
            emit("")
            emit("[本轮回答完成，%d 字]" % len(text))
            emit("")
        elif isinstance(event, TurnCompleted):
            emit("[对话结束]")

    emit("```")
    emit()
    emit("---")
    emit()
    emit("## 运行指标")
    emit()
    emit("| 指标 | 数值 |")
    emit("|---|---|")
    emit("| 模型请求轮次 | %d |" % max(rounds, 1))
    emit("| 工具调用次数 | %d |" % len(tool_calls))
    emit("| 调用序列 | %s |" % (" → ".join(tool_calls) or "无"))
    emit("| 端到端耗时 | %.1f 秒 |" % (time.time() - started))
    emit("| 回答字数 | %d |" % len("".join(answer)))

    out_dir = os.path.join(ROOT, "docs", "anti-fraud")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n已写入 %s" % out_path)
    return 0


def _brief(arguments) -> str:
    if not arguments:
        return ""
    parts = []
    for key, value in arguments.items():
        text = str(value).replace("\n", " ")
        parts.append("%s=%s" % (key, text if len(text) <= 60 else text[:57] + "..."))
    return ", ".join(parts)


def _one_line(text: str, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


if __name__ == "__main__":
    sys.exit(main())
