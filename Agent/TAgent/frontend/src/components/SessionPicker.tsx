/** 历史会话选择器：↑↓ 选择、Enter 恢复、Esc 关闭。 */
import { useEffect, useRef, useState } from "react";

import { relativeTime } from "../format";
import type { SessionSummary } from "../types";
import { IconClose, IconHistory } from "./icons";

interface SessionPickerProps {
  open: boolean;
  sessions: SessionSummary[];
  currentId: string;
  onClose: () => void;
  onResume: (id: string) => void;
}

export function SessionPicker({ open, sessions, currentId, onClose, onResume }: SessionPickerProps) {
  const [index, setIndex] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setIndex(0);
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (!sessions.length) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setIndex((prev) => (prev + 1) % sessions.length);
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setIndex((prev) => (prev - 1 + sessions.length) % sessions.length);
      }
      if (event.key === "Enter") {
        event.preventDefault();
        onResume(sessions[Math.min(index, sessions.length - 1)].id);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [index, onClose, onResume, open, sessions]);

  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>(".picker-row.is-active");
    active?.scrollIntoView({ block: "nearest" });
  }, [index]);

  if (!open) return null;

  return (
    <div className="overlay" onMouseDown={onClose}>
      <div className="picker" onMouseDown={(event) => event.stopPropagation()}>
        <div className="picker-head">
          <div className="picker-title">
            <IconHistory size={16} />
            历史会话
            <span className="picker-count">{sessions.length}</span>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} title="关闭">
            <IconClose />
          </button>
        </div>

        <div className="picker-list" ref={listRef}>
          {sessions.length === 0 && <div className="side-empty">还没有存档会话</div>}
          {sessions.map((item, i) => (
            <button
              key={item.id}
              type="button"
              className={`picker-row${i === index ? " is-active" : ""}${
                item.id === currentId ? " is-current" : ""
              }`}
              onMouseEnter={() => setIndex(i)}
              onClick={() => onResume(item.id)}
            >
              <div className="picker-row-main">
                <span className="picker-row-title">{item.title}</span>
                {item.id === currentId && <span className="badge">当前</span>}
              </div>
              <div className="picker-row-meta">
                {relativeTime(item.updated_at)} · {item.message_count} 条消息
                {item.preview ? ` · ${item.preview}` : ""}
              </div>
            </button>
          ))}
        </div>

        <div className="picker-foot">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> 选择
          </span>
          <span>
            <kbd>Enter</kbd> 恢复
          </span>
          <span>
            <kbd>Esc</kbd> 取消
          </span>
        </div>
      </div>
    </div>
  );
}
