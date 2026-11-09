/** 左侧边栏：会话身份、历史会话、MCP 服务器与工具。 */
import { useEffect, useState } from "react";

import { api } from "../api";
import { relativeTime } from "../format";
import type { McpServer, McpState, SessionPayload, SessionSummary } from "../types";
import { IconChevron, IconHistory, IconPlus, IconServer, IconSpark } from "./icons";

interface SidebarProps {
  open: boolean;
  session: SessionPayload | null;
  mcp: McpState | null;
  model: string;
  configSource: string;
  busy: boolean;
  onNew: () => void;
  onHistory: () => void;
  onResume: (id: string) => void;
}

export function Sidebar(props: SidebarProps) {
  const { open, session, mcp, model, configSource, busy, onNew, onHistory, onResume } = props;
  const [recent, setRecent] = useState<SessionSummary[]>([]);
  const [expanded, setExpanded] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    api
      .listSessions()
      .then((data) => !cancelled && setRecent(data.sessions.slice(0, 8)))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [session?.id]);

  if (!open) return null;

  const servers = mcp?.servers ?? [];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">T</div>
        <div className="brand-text">
          <strong>TAgent</strong>
          <span>本地智能体工作台</span>
        </div>
      </div>

      <button type="button" className="new-chat" onClick={onNew} disabled={busy}>
        <IconPlus size={15} />
        新建会话
        <kbd>Ctrl</kbd>
      </button>

      <div className="side-section">
        <div className="side-title">
          <span>当前会话</span>
        </div>
        <div className="current-session">
          <div className="current-title">{session?.title || "尚未命名"}</div>
          <div className="current-meta">
            <span className="mono">#{session?.id.slice(0, 8) ?? "--------"}</span>
            <span>{session?.message_count ?? 0} 条消息</span>
          </div>
          {session?.agents_doc && (
            <div className="current-doc" title={session.agents_doc}>
              AGENTS.md 已注入
            </div>
          )}
        </div>
      </div>

      <div className="side-section grow">
        <div className="side-title">
          <span>最近会话</span>
          <button type="button" className="side-link" onClick={onHistory}>
            <IconHistory size={13} />
            全部
          </button>
        </div>
        <div className="recent-list">
          {recent.length === 0 && <div className="side-empty">还没有存档会话</div>}
          {recent.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`recent-item${item.id === session?.id ? " is-current" : ""}`}
              onClick={() => onResume(item.id)}
              disabled={busy}
              title={item.preview || item.title}
            >
              <span className="recent-title">{item.title}</span>
              <span className="recent-meta">
                {relativeTime(item.updated_at)} · {item.message_count} 条
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="side-section">
        <div className="side-title">
          <span>MCP 服务器</span>
          <span className={`side-count${mcp?.loading ? " is-loading" : ""}`}>
            {mcp?.loading ? "连接中" : `${mcp?.connected_count ?? 0}/${mcp?.total_count ?? 0}`}
          </span>
        </div>
        {servers.length === 0 && (
          <div className="side-empty">
            {mcp?.loading ? "正在连接…" : "未配置 .mcp.json"}
          </div>
        )}
        {servers.map((server) => (
          <ServerRow
            key={server.key}
            server={server}
            expanded={expanded === server.key}
            onToggle={() => setExpanded(expanded === server.key ? "" : server.key)}
          />
        ))}
      </div>

      <div className="side-footer">
        <div className="footer-model">
          <IconSpark size={13} />
          <span>{model || "未配置模型"}</span>
        </div>
        <div className="footer-path" title={configSource}>
          {configSource ? shortenPath(configSource) : "默认配置"}
        </div>
      </div>
    </aside>
  );
}

function ServerRow(props: { server: McpServer; expanded: boolean; onToggle: () => void }) {
  const { server, expanded, onToggle } = props;
  const status = server.disabled ? "off" : server.connected ? "ok" : "bad";
  return (
    <div className={`server-row is-${status}`}>
      <button type="button" className="server-head" onClick={onToggle}>
        <span className={`server-dot is-${status}`} />
        <IconServer size={13} />
        <span className="server-name">{server.name}</span>
        <span className="server-count">{server.tool_count}</span>
        {server.tools.length > 0 && (
          <IconChevron className={`tool-chevron${expanded ? " is-open" : ""}`} />
        )}
      </button>
      {expanded && server.tools.length > 0 && (
        <div className="server-tools">
          {server.tools.map((tool) => (
            <div key={tool} className="server-tool" title={tool}>
              {tool.replace(/^mcp__/, "")}
            </div>
          ))}
        </div>
      )}
      {!server.connected && server.error && !expanded && (
        <div className="server-error">{server.error}</div>
      )}
    </div>
  );
}

function shortenPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/");
  return parts.length <= 3 ? normalized : `…/${parts.slice(-2).join("/")}`;
}
