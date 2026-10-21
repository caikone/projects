# frontend/ —— 浏览器前端工程（预留）

放 Web/移动端共用的**前端工程**（React / Vue / Svelte 等），与后端的 `web/` 分离。

与后端的唯一契约是 core 的事件流。后端（见 `../web/`）会把这些事件原样推给浏览器：

| 事件 | 含义 |
|---|---|
| `TurnStarted` | 一轮对话开始 |
| `AssistantTextDelta` | 模型文本增量（打字机） |
| `ToolCallStarted` / `ToolCallCompleted` | 工具调用开始 / 结束 |
| `RoundFinished` | 一次「请求 → 工具」循环结束 |
| `TurnCompleted` | 本轮结束，带最终答案 |
| `ErrorOccurred` | 可恢复错误 |

事件定义在 `core/domain/events.py`，字段即 SSE 的 JSON 结构，前端可直接消费。
