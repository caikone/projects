/**
 * 与 Python 网关的唯一通信层。
 *
 * 对话走 SSE：用 fetch + ReadableStream 手动解析帧，
 * 因为 `EventSource` 只支持 GET，没法把消息体发过去。
 */
import type {
  AgentEvent,
  Bootstrap,
  McpState,
  SessionPayload,
  SessionSummary,
  Usage,
} from "./types";

const JSON_HEADERS = { "Content-Type": "application/json" };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const raw = await response.text();
  const data = raw ? safeParse(raw) : {};
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    if (data && typeof data === "object" && "error" in data) {
      const detail = (data as { error?: unknown }).error;
      if (detail) message = String(detail);
    }
    throw new Error(message);
  }
  return data as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

export const api = {
  bootstrap: () => request<Bootstrap>("/api/bootstrap"),

  mcp: () => request<McpState>("/api/mcp"),

  cancel: () =>
    request<{ ok: boolean }>("/api/cancel", { method: "POST" }),

  newSession: () =>
    request<{ session: SessionPayload; context: Usage }>("/api/session/new", {
      method: "POST",
    }),

  saveSession: () =>
    request<{ ok: boolean; id: string; title: string; path: string }>(
      "/api/session/save",
      { method: "POST" },
    ),

  listSessions: () =>
    request<{ sessions: SessionSummary[]; enabled: boolean; current: string }>(
      "/api/sessions",
    ),

  resume: (id: string) =>
    request<{ session: SessionPayload; context: Usage }>(
      "/api/session/resume",
      { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ id }) },
    ),

  compact: () =>
    request<{
      ok: boolean;
      archive_id: string;
      archive_path: string;
      tokens_before: number;
      tokens_after: number;
      message_count: number;
      session: SessionPayload;
      context: Usage;
    }>("/api/compact", { method: "POST" }),

  /** 流式对话；返回的 Promise 在一轮结束时 resolve。 */
  async chat(
    text: string,
    onEvent: (event: AgentEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ text }),
      signal,
    });

    if (!response.ok) {
      const raw = await response.text();
      const data = safeParse(raw) as { error?: string };
      throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
    }
    if (!response.body) throw new Error("当前浏览器不支持流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (dispatch(frame, onEvent)) return;
        boundary = buffer.indexOf("\n\n");
      }
    }
  },
};

/** 解析一帧 SSE；返回 true 表示收到 [DONE]。 */
function dispatch(frame: string, onEvent: (event: AgentEvent) => void): boolean {
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data:")) continue; // ": ping" 心跳帧直接跳过
    const payload = line.slice(5).trim();
    if (!payload) continue;
    if (payload === "[DONE]") return true;
    try {
      onEvent(JSON.parse(payload) as AgentEvent);
    } catch {
      // 半帧/脏数据忽略即可：下一帧会重新对齐
    }
  }
  return false;
}
