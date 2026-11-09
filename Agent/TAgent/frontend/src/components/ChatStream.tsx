/** 对话流：自动跟随底部 + 空状态引导。 */
import { useEffect, useRef } from "react";

import type { ChatMessage } from "../types";
import { MessageBubble } from "./MessageBubble";
import { IconLayers, IconShield, IconSpark, IconTool } from "./icons";

interface ChatStreamProps {
  messages: ChatMessage[];
  busy: boolean;
  onSuggestion: (text: string) => void;
}

const SUGGESTIONS = [
  {
    icon: <IconShield size={16} />,
    title: "这条信息是诈骗吗",
    text: "我收到一条短信，麻烦帮我判断是不是诈骗：\n（把短信原文、或来电说了什么粘贴在这里）",
  },
  {
    icon: <IconTool size={16} />,
    title: "要我转到安全账户",
    text: "刚接到电话，对方说是公安局的，说我涉嫌洗钱，要我把钱转到「安全账户」验资，还不让我告诉家人。要不要转？",
  },
  {
    icon: <IconLayers size={16} />,
    title: "家人疑似被骗",
    text: "我爸妈最近在跟一个「投资导师」学炒股，已经投了几万块，说稳赚不赔。我该怎么劝？",
  },
  {
    icon: <IconSpark size={16} />,
    title: "拆解套路 / 做演练",
    text: "带我拆解一下「刷单返利」这类骗局的完整剧本，我想知道每一步他们是怎么让人上钩的。",
  },
];

export function ChatStream({ messages, busy, onSuggestion }: ChatStreamProps) {
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
            <IconShield size={22} />
          </div>
          <h1>
            先把可疑信息<span className="grad-text">发给我</span>
          </h1>
          <p className="hero-sub">
            短信、来电、链接、聊天记录、投资平台、转账要求——先说清风险等级，再谈怎么办。
          </p>
          <div className="hero-grid">
            {SUGGESTIONS.map((item) => (
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
