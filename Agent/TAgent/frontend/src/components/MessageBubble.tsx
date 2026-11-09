/** 单条消息：用户气泡 / 助手分块 / 系统提示。 */
import { useState } from "react";

import { formatClock } from "../format";
import type { ChatMessage } from "../types";
import { IconCheck, IconCopy, IconSpark } from "./icons";
import { Markdown } from "./Markdown";
import { ToolCard } from "./ToolCard";

export function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="row row-user">
        <div className="bubble-user">
          <div className="user-text">{message.text}</div>
        </div>
      </div>
    );
  }

  if (message.role === "system") {
    return (
      <div className="row row-system">
        <div className="alert">
          <span className="alert-dot" />
          <div>
            <strong>出错了</strong>
            <p>{message.text}</p>
          </div>
        </div>
      </div>
    );
  }

  const lastBlock = message.blocks[message.blocks.length - 1];
  const showCaret =
    message.streaming && (!lastBlock || (lastBlock.kind === "text" && !lastBlock.done));

  return (
    <div className="row row-assistant">
      <div className="avatar">
        <IconSpark size={15} />
      </div>
      <div className="assistant-body">
        {message.blocks.map((block, index) =>
          block.kind === "text" ? (
            <div className="assistant-text" key={block.id}>
              <Markdown text={block.content} />
              {showCaret && index === message.blocks.length - 1 && (
                <span className="caret" aria-hidden="true" />
              )}
            </div>
          ) : (
            <ToolCard key={block.id} block={block} />
          ),
        )}

        {message.blocks.length === 0 && message.streaming && (
          <div className="thinking">
            <span />
            <span />
            <span />
            <em>正在思考</em>
          </div>
        )}

        {message.blocks.length === 0 && !message.streaming && (
          <div className="empty-turn">
            {message.meta?.cancelled ? "已停止，本轮没有产生内容" : "（本轮没有内容）"}
          </div>
        )}

        {!message.streaming && message.blocks.length > 0 && (
          <div className="assistant-foot">
            <CopyAnswer blocks={message.blocks} />
            {message.meta && (
              <span className="foot-meta">
                {message.meta.rounds ? `${message.meta.rounds} 轮` : ""}
                {message.meta.tools ? ` · ${message.meta.tools} 次工具` : ""}
                {message.meta.cancelled ? " · 已停止" : ""}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CopyAnswer({ blocks }: { blocks: ChatMessage["blocks"] }) {
  const [copied, setCopied] = useState(false);
  const text = blocks
    .filter((block): block is Extract<typeof block, { kind: "text" }> => block.kind === "text")
    .map((block) => block.content)
    .join("\n\n");

  if (!text) return null;

  return (
    <button
      type="button"
      className="ghost-btn tiny"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
      {copied ? "已复制" : "复制回答"}
    </button>
  );
}

export function Timestamp({ ts }: { ts: number }) {
  return <span className="timestamp">{formatClock(ts)}</span>;
}
