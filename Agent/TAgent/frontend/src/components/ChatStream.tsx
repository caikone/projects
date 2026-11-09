/** 对话流：自动跟随底部 + 空状态引导。 */
import { useEffect, useRef } from "react";

import type { ChatMessage } from "../types";
import { MessageBubble } from "./MessageBubble";
import { IconLayers, IconServer, IconSpark, IconTool } from "./icons";

interface ChatStreamProps {
  messages: ChatMessage[];
  busy: boolean;
  onSuggestion: (text: string) => void;
  hasMcp: boolean;
}

const SUGGESTIONS = [
  {
    icon: <IconLayers size={16} />,
    title: "梳理这个项目",
    text: "帮我快速梳理当前项目的目录结构与核心模块，指出最值得优先阅读的三个文件。",
  },
  {
    icon: <IconTool size={16} />,
    title: "调用工具试试",
    text: "用 RunCli 看一下当前目录的文件列表，并按类型归类说明。",
  },
  {
    icon: <IconServer size={16} />,
    title: "检查 MCP",
    text: "列出当前已连接的 MCP 服务器与它们暴露的工具，说明各自适合做什么。",
  },
  {
    icon: <IconSpark size={16} />,
    title: "写一段代码",
    text: "用 Python 写一个把 Markdown 转成纯文本的小脚本，要求带命令行参数与简单测试。",
  },
];

export function ChatStream({ messages, busy, onSuggestion, hasMcp }: ChatStreamProps) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const follow = useRef(true);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      follow.current = distance < 180;
    };
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => node.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const node = scroller.current;
    if (!node || !follow.current) return;
    node.scrollTop = node.scrollHeight;
  }, [messages]);

  const visible = messages.filter(
    (message) =>
      message.role !== "assistant" ||
      message.blocks.length > 0 ||
      message.streaming ||
      Boolean(message.meta), // 被停止/失败的一轮：没有内容也要给个交代
  );

  return (
    <div className="chat-stream" ref={scroller}>
      {visible.length === 0 ? (
        <div className="hero">
          <div className="hero-glyph">
            <IconSpark size={22} />
          </div>
          <h1>
            让 TAgent 替你<span className="grad-text">跑一趟</span>
          </h1>
          <p className="hero-sub">
            本地文件、命令行、网页抓取、MCP 外部服务——一句需求，它自己挑工具。
          </p>
          <div className="hero-grid">
            {SUGGESTIONS.filter((item) => hasMcp || item.title !== "检查 MCP").map((item) => (
              <button
                key={item.title}
                type="button"
                className="suggestion"
                onClick={() => onSuggestion(item.text)}
              >
                <span className="suggestion-icon">{item.icon}</span>
                <span className="suggestion-title">{item.title}</span>
                <span className="suggestion-text">{item.text}</span>
              </button>
            ))}
          </div>
          <div className="hero-tips">
            <span>
              <kbd>/</kbd> 命令
            </span>
            <span>
              <kbd>@</kbd> 工具
            </span>
            <span>
              <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行
            </span>
            {busy && <span className="hero-busy">正在处理上一轮…</span>}
          </div>
        </div>
      ) : (
        <div className="thread">
          {visible.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
        </div>
      )}
    </div>
  );
}
