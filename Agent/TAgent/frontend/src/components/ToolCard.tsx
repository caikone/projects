/** 一次工具调用的卡片：状态、参数、结果预览都可折叠。 */
import { useState } from "react";

import { formatElapsed, prettyJSON, truncate } from "../format";
import type { Block } from "../types";
import { IconChevron, IconCopy, IconTool } from "./icons";

type ToolBlock = Extract<Block, { kind: "tool" }>;

export function ToolCard({ block }: { block: ToolBlock }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const running = block.status === "running";
  const failed = block.status === "fail";

  const statusText = running ? "执行中" : failed ? "失败" : "完成";
  const preview = truncate(block.content.replace(/\s+\n/g, "\n").trim(), 220);

  return (
    <div className={`tool-card${running ? " is-running" : failed ? " is-failed" : " is-ok"}`}>
      <button type="button" className="tool-head" onClick={() => setOpen((v) => !v)}>
        <span className={`tool-status${running ? " pulse" : ""}`} />
        <IconTool />
        <span className="tool-name">{block.name}</span>
        <span className="tool-round">第 {block.round} 轮</span>
        <span className="tool-spacer" />
        {block.elapsedMs > 0 && <span className="tool-elapsed">{formatElapsed(block.elapsedMs)}</span>}
        <span className="tool-state">{statusText}</span>
        <IconChevron className={`tool-chevron${open ? " is-open" : ""}`} />
      </button>

      {!open && preview && (
        <div className="tool-preview">
          <code>{preview}</code>
        </div>
      )}

      {open && (
        <div className="tool-body">
          <div className="tool-section">
            <div className="tool-section-head">
              <span>参数</span>
              <button
                type="button"
                className="mini-btn"
                onClick={() => {
                  navigator.clipboard?.writeText(prettyJSON(block.args));
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                }}
              >
                <IconCopy />
                {copied ? "已复制" : "复制"}
              </button>
            </div>
            <pre className="tool-pre">{prettyJSON(block.args)}</pre>
          </div>
          {block.content && (
            <div className="tool-section">
              <div className="tool-section-head">
                <span>结果</span>
                <span className="tool-chars">{block.content.length} 字符</span>
              </div>
              <pre className="tool-pre">{block.content}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
