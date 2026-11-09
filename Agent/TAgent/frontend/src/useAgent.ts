/**
 * 一处集中的界面状态机：bootstrap → 流式对话 → 会话切换。
 *
 * 设计原则：**事件只做增量合并**。SSE 里每个事件都映射到一个确定的状态变更，
 * 组件层只负责画，不再自己拼字符串——这样「工具调用夹在文字中间」这类
 * 结构才不会在流式过程中抖动。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type {
  AgentEvent,
  Block,
  Bootstrap,
  ChatMessage,
  McpState,
  SessionPayload,
  SessionSummary,
  ToolInfo,
  Usage,
} from "./types";

const EMPTY_USAGE: Usage = {
  used_tokens: 0,
  window_tokens: 128000,
  percent: 0,
  window_is_default: true,
  from_api: false,
  threshold: 0.8,
};

let seq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq += 1)}`;

/** 把服务端的「角色消息流」还原成界面上的「用户气泡 + 助手分块」。 */
export function toChatMessages(session: SessionPayload): ChatMessage[] {
  const result: ChatMessage[] = [];
  const toolIndex = new Map<string, Block>();

  for (const message of session.messages) {
    if (message.role === "system") continue;

    if (message.role === "user") {
      result.push({
        id: message.id,
        role: "user",
        text: message.content,
        blocks: [],
        streaming: false,
        ts: Date.now(),
        turnId: 0,
      });
      continue;
    }

    if (message.role === "assistant") {
      const blocks: Block[] = [];
      if (message.content.trim()) {
        blocks.push({
          kind: "text",
          id: uid("t"),
          content: message.content,
          done: true,
        });
      }
      for (const call of message.tool_calls || []) {
        const block: Block = {
          kind: "tool",
          id: uid("k"),
          callId: call.id,
          name: call.name,
          args: call.arguments,
          status: "ok",
          content: "",
          elapsedMs: 0,
          round: 1,
        };
        blocks.push(block);
        toolIndex.set(call.id, block);
      }
      if (blocks.length === 0) continue;
      result.push({
        id: message.id,
        role: "assistant",
        text: "",
        blocks,
        streaming: false,
        ts: Date.now(),
        turnId: 0,
      });
      continue;
    }

    // tool 消息：把结果贴回对应的调用卡片
    const target = toolIndex.get(message.tool_call_id);
    if (target && target.kind === "tool") {
      target.content = message.content;
      target.status = "ok";
    }
  }
  return result;
}

export function useAgent() {
  const [booting, setBooting] = useState(true);
  const [fatal, setFatal] = useState<string>("");
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [usage, setUsage] = useState<Usage>(EMPTY_USAGE);
  const [mcp, setMcp] = useState<McpState | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [model, setModel] = useState<Bootstrap["model"] | null>(null);
  const [meta, setMeta] = useState<Bootstrap["meta"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string>("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionPickerOpen, setSessionPickerOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const streamingIdRef = useRef<string>("");

  /* ---------------------------------------------------------------- */
  /* 初始化                                                            */
  /* ---------------------------------------------------------------- */
  const applyBootstrap = useCallback((data: Bootstrap) => {
    setSession(data.session);
    setMessages(toChatMessages(data.session));
    setUsage(data.context);
    setMcp(data.mcp);
    setTools(data.tools);
    setModel(data.model);
    setMeta(data.meta);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .bootstrap()
      .then((data) => {
        if (!cancelled) applyBootstrap(data);
      })
      .catch((error: Error) => !cancelled && setFatal(error.message))
      .finally(() => !cancelled && setBooting(false));
    return () => {
      cancelled = true;
    };
  }, [applyBootstrap]);

  /* MCP 是后台加载的：先出界面，加载完再补一次数据 */
  useEffect(() => {
    if (!mcp?.loading) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.mcp();
        setMcp(next);
        if (!next.loading) {
          window.clearInterval(timer);
          const full = await api.bootstrap();
          setTools(full.tools);
          setMcp(full.mcp);
        }
      } catch {
        window.clearInterval(timer);
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [mcp?.loading]);

  /* ---------------------------------------------------------------- */
  /* 事件合并                                                          */
  /* ---------------------------------------------------------------- */
  const patchAssistant = useCallback(
    (mutate: (message: ChatMessage) => void) => {
      const id = streamingIdRef.current;
      if (!id) return;
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id !== id) return message;
          const next: ChatMessage = { ...message, blocks: message.blocks.map((b) => ({ ...b })) };
          mutate(next);
          return next;
        }),
      );
    },
    [],
  );

  const handleEvent = useCallback(
    (event: AgentEvent) => {
      switch (event.type) {
        case "UserAccepted": {
          const assistantId = uid("a");
          streamingIdRef.current = assistantId;
          setMessages((prev) => [
            ...prev,
            {
              id: event.message.id || uid("u"),
              role: "user",
              text: event.message.content,
              blocks: [],
              streaming: false,
              ts: Date.now(),
              turnId: 0,
            },
            {
              id: assistantId,
              role: "assistant",
              text: "",
              blocks: [],
              streaming: true,
              ts: Date.now(),
              turnId: 0,
            },
          ]);
          setBusy(true);
          break;
        }

        case "AssistantTextDelta": {
          patchAssistant((message) => {
            const last = message.blocks[message.blocks.length - 1];
            if (last && last.kind === "text" && !last.done) {
              last.content += event.text;
            } else {
              message.blocks.push({
                kind: "text",
                id: uid("t"),
                content: event.text,
                done: false,
              });
            }
          });
          break;
        }

        case "ToolCallStarted": {
          patchAssistant((message) => {
            const last = message.blocks[message.blocks.length - 1];
            if (last && last.kind === "text") last.done = true;
            message.blocks.push({
              kind: "tool",
              id: uid("k"),
              callId: event.call_id,
              name: event.name,
              args: event.arguments,
              status: "running",
              content: "",
              elapsedMs: 0,
              round: event.round_index,
            });
          });
          break;
        }

        case "ToolCallCompleted": {
          const result = event.result;
          if (!result) break;
          patchAssistant((message) => {
            for (const block of message.blocks) {
              if (block.kind === "tool" && block.callId === result.call_id) {
                block.status = result.ok ? "ok" : "fail";
                block.content = result.content;
                block.elapsedMs = result.elapsed_ms;
              }
            }
          });
          break;
        }

        case "AssistantMessageCompleted": {
          // 以服务端全文为准：流式增量可能有掉帧，这里做一次对齐
          patchAssistant((message) => {
            const last = message.blocks[message.blocks.length - 1];
            if (event.content && last && last.kind === "text" && !last.done) {
              last.content = event.content;
              last.done = true;
            }
          });
          break;
        }

        case "ContextUsageUpdated": {
          setUsage((prev) => ({
            ...prev,
            used_tokens: event.used_tokens,
            window_tokens: event.window_tokens,
            percent: event.percent,
            window_is_default: event.window_is_default,
            from_api: event.from_api,
          }));
          break;
        }

        case "TurnCompleted": {
          patchAssistant((message) => {
            message.streaming = false;
            message.meta = {
              rounds: event.rounds,
              tools: event.tool_calls,
              cancelled: event.cancelled,
            };
            for (const block of message.blocks) {
              if (block.kind === "text") block.done = true;
            }
          });
          streamingIdRef.current = "";
          setBusy(false);
          break;
        }

        case "ErrorOccurred": {
          setMessages((prev) => [
            ...prev,
            {
              id: uid("e"),
              role: "system",
              text: event.message,
              blocks: [],
              streaming: false,
              ts: Date.now(),
              turnId: 0,
            },
          ]);
          setBusy(false);
          streamingIdRef.current = "";
          break;
        }

        case "SessionSaved": {
          setUsage(event.context);
          setSession(event.session);
          break;
        }

        default:
          break;
      }
    },
    [patchAssistant],
  );

  /* ---------------------------------------------------------------- */
  /* 动作                                                              */
  /* ---------------------------------------------------------------- */
  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busy) return;
      const controller = new AbortController();
      abortRef.current = controller;
      setNotice("");
      try {
        await api.chat(content, handleEvent, controller.signal);
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setNotice((error as Error).message);
        }
        setBusy(false);
        streamingIdRef.current = "";
        setMessages((prev) =>
          prev.map((message) =>
            message.streaming
              ? { ...message, streaming: false, meta: { ...message.meta, cancelled: true } }
              : message,
          ),
        );
      } finally {
        abortRef.current = null;
      }
    },
    [busy, handleEvent],
  );

  const stop = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await api.cancel();
    } catch {
      /* 忽略 */
    }
    setBusy(false);
  }, []);

  const newSession = useCallback(async () => {
    if (busy) return;
    try {
      const data = await api.newSession();
      setSession(data.session);
      setMessages(toChatMessages(data.session));
      setUsage(data.context);
      setSessionPickerOpen(false);
    } catch (error) {
      setNotice((error as Error).message);
    }
  }, [busy]);

  const save = useCallback(async () => {
    try {
      const data = await api.saveSession();
      setNotice(`已保存：${data.title}`);
      window.setTimeout(() => setNotice(""), 3200);
    } catch (error) {
      setNotice((error as Error).message);
    }
  }, []);

  const openSessionPicker = useCallback(async () => {
    try {
      const data = await api.listSessions();
      setSessions(data.sessions);
      setSessionPickerOpen(true);
    } catch (error) {
      setNotice((error as Error).message);
    }
  }, []);

  const resume = useCallback(
    async (id: string) => {
      if (busy) return;
      try {
        const data = await api.resume(id);
        setSession(data.session);
        setMessages(toChatMessages(data.session));
        setUsage(data.context);
        setSessionPickerOpen(false);
      } catch (error) {
        setNotice((error as Error).message);
      }
    },
    [busy],
  );

  const compact = useCallback(async () => {
    if (busy) return;
    setNotice("正在压缩上下文…");
    try {
      const data = await api.compact();
      setSession(data.session);
      setMessages(toChatMessages(data.session));
      setUsage(data.context);
      setNotice(
        `已压缩：${data.tokens_before} → ${data.tokens_after} tokens（归档 ${data.archive_id}）`,
      );
      window.setTimeout(() => setNotice(""), 5000);
    } catch (error) {
      setNotice((error as Error).message);
    }
  }, [busy]);

  const stats = useMemo(() => {
    let toolCalls = 0;
    let rounds = 0;
    for (const message of messages) {
      for (const block of message.blocks) {
        if (block.kind === "tool") toolCalls += 1;
      }
      // 实时事件带精确轮次；历史回放时按「一条助手消息 ≈ 一轮」估算
      rounds += message.meta?.rounds ?? (message.role === "assistant" && message.blocks.length > 0 ? 1 : 0);
    }
    return { toolCalls, rounds };
  }, [messages]);

  return {
    booting,
    fatal,
    session,
    messages,
    usage,
    mcp,
    tools,
    model,
    meta,
    busy,
    notice,
    sessions,
    sessionPickerOpen,
    stats,
    actions: {
      send,
      stop,
      newSession,
      save,
      compact,
      resume,
      openSessionPicker,
      closeSessionPicker: () => setSessionPickerOpen(false),
      clearNotice: () => setNotice(""),
    },
  };
}
