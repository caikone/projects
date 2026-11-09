/**
 * 前后端共用的数据契约。
 *
 * 后端 `web/server.py` 直接序列化 core 的 dataclass，字段名与
 * `core/domain/events.py` 一一对应——这里只是给它们加上类型。
 */

export type Role = "user" | "assistant" | "tool" | "system";

export interface ToolCallInfo {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ServerMessage {
  id: string;
  index: number;
  role: Role;
  content: string;
  name: string;
  tool_call_id: string;
  tool_calls: ToolCallInfo[];
  ts: number;
}

export interface SessionPayload {
  id: string;
  title: string;
  agents_doc: string;
  message_count: number;
  messages: ServerMessage[];
}

export interface Usage {
  used_tokens: number;
  window_tokens: number;
  percent: number;
  window_is_default: boolean;
  from_api: boolean;
  threshold: number;
}

export interface McpServer {
  name: string;
  key: string;
  transport: string;
  endpoint: string;
  connected: boolean;
  disabled: boolean;
  tool_count: number;
  server_info: string;
  error: string;
  note: string;
  tools: string[];
}

export interface McpState {
  loading: boolean;
  loaded: boolean;
  servers: McpServer[];
  total_tools: number;
  connected_count: number;
  total_count: number;
  summary: string;
  config_error: string;
  notices: string[];
}

export interface ToolInfo {
  name: string;
  description: string;
}

export interface Bootstrap {
  session: SessionPayload;
  model: { name: string; base_url: string; temperature: number | null };
  tools: ToolInfo[];
  mcp: McpState;
  context: Usage;
  meta: {
    config_source: string;
    config_created: boolean;
    skills: string;
    agents_doc: string;
    sessions: string;
    cwd: string;
    busy: boolean;
    version: string;
  };
}

export interface SessionSummary {
  id: string;
  title: string;
  updated_at: number;
  message_count: number;
  preview: string;
  path: string;
}

export interface ToolResultPayload {
  call_id: string;
  name: string;
  content: string;
  ok: boolean;
  elapsed_ms: number;
}

/* ------------------------------------------------------------------ */
/* SSE 事件：类型名就是后端 dataclass 的类名                            */
/* ------------------------------------------------------------------ */
export type AgentEvent =
  | { type: "UserAccepted"; message: ServerMessage }
  | { type: "TurnStarted"; turn_id: number; user_text: string }
  | { type: "AssistantTextDelta"; turn_id: number; text: string }
  | {
      type: "ToolCallStarted";
      turn_id: number;
      call_id: string;
      name: string;
      arguments: Record<string, unknown>;
      arguments_json: string;
      round_index: number;
    }
  | {
      type: "ToolCallCompleted";
      turn_id: number;
      round_index: number;
      result: ToolResultPayload | null;
    }
  | { type: "RoundFinished"; turn_id: number; round_index: number; has_tool_calls: boolean; reached_limit: boolean }
  | { type: "AssistantMessageCompleted"; turn_id: number; round_index: number; content: string }
  | { type: "TurnCompleted"; turn_id: number; answer: string; rounds: number; tool_calls: number; cancelled: boolean }
  | { type: "ErrorOccurred"; message: string; error_type: string; recoverable: boolean }
  | {
      type: "ContextUsageUpdated";
      turn_id: number;
      used_tokens: number;
      window_tokens: number;
      percent: number;
      window_is_default: boolean;
      warning: boolean;
      from_api: boolean;
    }
  | { type: "SessionSaved"; session: SessionPayload; context: Usage }
  | { type: "ContextCompactStarted"; archive_id: string }
  | { type: "ContextCompacted"; archive_id: string; tokens_before: number; tokens_after: number };

/* ------------------------------------------------------------------ */
/* 界面内部模型                                                        */
/* ------------------------------------------------------------------ */
export type Block =
  | { kind: "text"; id: string; content: string; done: boolean }
  | {
      kind: "tool";
      id: string;
      callId: string;
      name: string;
      args: unknown;
      status: "running" | "ok" | "fail";
      content: string;
      elapsedMs: number;
      round: number;
    };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  /** 用户消息的纯文本 */
  text: string;
  /** 助手消息的分块内容（文字 + 工具调用交替） */
  blocks: Block[];
  streaming: boolean;
  ts: number;
  turnId: number;
  meta?: { rounds?: number; tools?: number; cancelled?: boolean };
}
