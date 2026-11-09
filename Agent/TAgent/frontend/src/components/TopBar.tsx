/** 顶栏：会话身份、关键动作、上下文用量。 */
import type { SessionPayload, Usage } from "../types";
import {
  IconHistory,
  IconPanel,
  IconPlus,
  IconSave,
  IconSidebar,
} from "./icons";

interface TopBarProps {
  session: SessionPayload | null;
  model: string;
  busy: boolean;
  usage: Usage;
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  onToggleSidebar: () => void;
  onToggleInspector: () => void;
  onNew: () => void;
  onSave: () => void;
  onHistory: () => void;
}

export function TopBar(props: TopBarProps) {
  const {
    session,
    model,
    busy,
    usage,
    sidebarOpen,
    inspectorOpen,
    onToggleSidebar,
    onToggleInspector,
    onNew,
    onSave,
    onHistory,
  } = props;

  const title = session?.title || "新会话";
  const percent = Math.min(100, usage.percent || 0);

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          type="button"
          className={`icon-btn${sidebarOpen ? " is-on" : ""}`}
          onClick={onToggleSidebar}
          title="侧边栏"
        >
          <IconSidebar />
        </button>
        <div className="session-chip">
          <span className={`live-dot${busy ? " is-busy" : ""}`} />
          <span className="session-name">{title}</span>
          <span className="session-id">#{session?.id.slice(0, 6) ?? "------"}</span>
        </div>
      </div>

      <div className="topbar-right">
        <span className="model-pill" title="当前模型">
          {model || "—"}
        </span>

        <div className="ctx-meter" title={`上下文 ${usage.used_tokens} / ${usage.window_tokens} tokens`}>
          <svg viewBox="0 0 36 36" className="ctx-ring" aria-hidden="true">
            <circle className="ring-track" cx="18" cy="18" r="15" />
            <circle
              className={`ring-fill${percent >= usage.threshold * 100 ? " is-warn" : ""}`}
              cx="18"
              cy="18"
              r="15"
              strokeDasharray={`${(percent / 100) * 94.2} 94.2`}
            />
          </svg>
          <span className="ctx-text">
            {Math.round(percent)}%
            <em>
              {compactNumber(usage.used_tokens)} / {compactNumber(usage.window_tokens)}
              {usage.window_is_default ? "（默认）" : ""}
            </em>
          </span>
        </div>

        <span className="topbar-divider" />

        <button type="button" className="ghost-btn" onClick={onHistory} title="历史会话">
          <IconHistory />
          <span>历史</span>
        </button>
        <button type="button" className="ghost-btn" onClick={onSave} title="保存会话（Ctrl+S）">
          <IconSave />
          <span>保存</span>
        </button>
        <button type="button" className="primary-btn" onClick={onNew} title="新建会话">
          <IconPlus />
          <span>新会话</span>
        </button>

        <button
          type="button"
          className={`icon-btn${inspectorOpen ? " is-on" : ""}`}
          onClick={onToggleInspector}
          title="检查器"
        >
          <IconPanel />
        </button>
      </div>
    </header>
  );
}

function compactNumber(value: number): string {
  if (value < 1000) return String(value);
  const k = value / 1000;
  return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}k`;
}
