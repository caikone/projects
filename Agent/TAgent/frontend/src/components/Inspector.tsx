/** 右侧检查器：上下文健康度、运行统计、工具清单。 */
import { useMemo, useState } from "react";

import { formatTokens } from "../format";
import type { McpState, ToolInfo, Usage } from "../types";
import { IconLayers, IconServer, IconTool } from "./icons";

interface InspectorProps {
  open: boolean;
  usage: Usage;
  tools: ToolInfo[];
  mcp: McpState | null;
  stats: { rounds: number; toolCalls: number };
  messageCount: number;
  onCompact: () => void;
}

export function Inspector(props: InspectorProps) {
  const { open, usage, tools, mcp, stats, messageCount, onCompact } = props;
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return tools;
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(query) ||
        tool.description.toLowerCase().includes(query),
    );
  }, [filter, tools]);

  if (!open) return null;

  const percent = Math.min(100, usage.percent || 0);
  const circumference = 2 * Math.PI * 30;
  const mcpTools = tools.filter((tool) => tool.name.startsWith("mcp__")).length;

  return (
    <aside className="inspector">
      <div className="side-title">
        <span>上下文</span>
        <span className="side-count">{usage.from_api ? "API 实测" : "本地估算"}</span>
      </div>

      <div className="ctx-card">
        <svg viewBox="0 0 76 76" className="ctx-big" aria-hidden="true">
          <circle className="ring-track" cx="38" cy="38" r="30" />
          <circle
            className={`ring-fill${percent >= usage.threshold * 100 ? " is-warn" : ""}`}
            cx="38"
            cy="38"
            r="30"
            style={{
              strokeDasharray: `${(percent / 100) * circumference} ${circumference}`,
            }}
          />
        </svg>
        <div className="ctx-center">
          <strong>{percent}%</strong>
          <span>已使用</span>
        </div>
        <div className="ctx-numbers">
          <div>
            <em>{formatTokens(usage.used_tokens)}</em>
            <span>当前</span>
          </div>
          <div>
            <em>
              {formatTokens(usage.window_tokens)}
              <sup>{usage.window_is_default ? "默认" : ""}</sup>
            </em>
            <span>窗口</span>
          </div>
        </div>
        <button type="button" className="ghost-btn wide" onClick={onCompact}>
          压缩上下文
        </button>
      </div>

      <div className="stat-grid">
        <div className="stat">
          <strong>{messageCount}</strong>
          <span>消息</span>
        </div>
        <div className="stat">
          <strong>{stats.toolCalls}</strong>
          <span>工具调用</span>
        </div>
        <div className="stat">
          <strong>{stats.rounds || "—"}</strong>
          <span>模型轮次</span>
        </div>
        <div className="stat">
          <strong>{mcp?.connected_count ?? 0}</strong>
          <span>MCP 在线</span>
        </div>
      </div>

      <div className="side-title tools-title">
        <span>工具</span>
        <span className="side-count">
          {tools.length} 个{mcpTools > 0 ? ` · MCP ${mcpTools}` : ""}
        </span>
      </div>

      <input
        className="filter-input"
        placeholder="过滤工具…"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />

      <div className="tool-list">
        {filtered.map((tool) => (
          <div key={tool.name} className={`tool-item${tool.name.startsWith("mcp__") ? " is-mcp" : ""}`}>
            <div className="tool-item-head">
              {tool.name.startsWith("mcp__") ? <IconServer size={12} /> : <IconLayers size={12} />}
              <span className="tool-item-name">{tool.name}</span>
            </div>
            {tool.description && <p className="tool-item-desc">{tool.description}</p>}
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="side-empty">
            <IconTool size={14} /> 没有匹配的工具
          </div>
        )}
      </div>
    </aside>
  );
}
