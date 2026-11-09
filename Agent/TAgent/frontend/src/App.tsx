import { useEffect, useState } from "react";

import { ChatStream } from "./components/ChatStream";
import { Composer } from "./components/Composer";
import { Inspector } from "./components/Inspector";
import { SessionPicker } from "./components/SessionPicker";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { useAgent } from "./useAgent";

export default function App() {
  const agent = useAgent();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);

  /* 全局快捷键：保存 / 关弹层 */
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void agent.actions.save();
      }
      if (event.key === "Escape" && agent.sessionPickerOpen) {
        agent.actions.closeSessionPicker();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [agent.actions, agent.sessionPickerOpen]);

  const handleCommand = (name: string) => {
    switch (name) {
      case "/new":
        void agent.actions.newSession();
        break;
      case "/resume":
        void agent.actions.openSessionPicker();
        break;
      case "/save":
        void agent.actions.save();
        break;
      case "/compact":
        void agent.actions.compact();
        break;
      default:
        break;
    }
  };

  if (agent.booting) {
    return (
      <div className="boot">
        <div className="boot-mark">T</div>
        <div className="boot-text">正在唤醒 TAgent…</div>
      </div>
    );
  }

  if (agent.fatal) {
    return (
      <div className="boot">
        <div className="boot-error">
          <strong>连不上后端</strong>
          <p>{agent.fatal}</p>
          <p className="hint">
            确认已执行 <code>python run.py --ui web</code>，并且端口与 config.ini 的 [web] 段一致。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        session={agent.session}
        mcp={agent.mcp}
        model={agent.model?.name ?? ""}
        configSource={agent.meta?.config_source ?? ""}
        busy={agent.busy}
        onNew={() => void agent.actions.newSession()}
        onHistory={() => void agent.actions.openSessionPicker()}
        onResume={(id) => void agent.actions.resume(id)}
      />

      <main className="workspace">
        <TopBar
          session={agent.session}
          model={agent.model?.name ?? ""}
          busy={agent.busy}
          usage={agent.usage}
          sidebarOpen={sidebarOpen}
          inspectorOpen={inspectorOpen}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onToggleInspector={() => setInspectorOpen((v) => !v)}
          onNew={() => void agent.actions.newSession()}
          onSave={() => void agent.actions.save()}
          onHistory={() => void agent.actions.openSessionPicker()}
        />

        <ChatStream
          messages={agent.messages}
          busy={agent.busy}
          onSuggestion={(text) => void agent.actions.send(text)}
        />

        <Composer
          sdkReady
          busy={agent.busy}
          tools={agent.tools}
          onSend={(text) => void agent.actions.send(text)}
          onStop={() => void agent.actions.stop()}
          onCommand={handleCommand}
        />
      </main>

      <Inspector
        open={inspectorOpen}
        usage={agent.usage}
        tools={agent.tools}
        mcp={agent.mcp}
        stats={agent.stats}
        messageCount={agent.session?.message_count ?? 0}
        onCompact={() => void agent.actions.compact()}
      />

      <SessionPicker
        open={agent.sessionPickerOpen}
        sessions={agent.sessions}
        currentId={agent.session?.id ?? ""}
        onClose={agent.actions.closeSessionPicker}
        onResume={(id) => void agent.actions.resume(id)}
      />

      {agent.notice && (
        <div className="toast" role="status" onClick={agent.actions.clearNotice}>
          {agent.notice}
        </div>
      )}
    </div>
  );
}
