# web/ —— Web 界面（Python 侧网关）

浏览器界面由两部分组成，这里只负责「后端」那一半：

```
web/frontend.py    Frontend 实现：run.py --ui web 的入口（起 uvicorn / 算静态目录 / 开浏览器）
web/server.py      Starlette 应用：HTTP API + SSE + 静态资源托管
../frontend/       React + TypeScript + Slate 工程（构建到 frontend/dist）
```

## 为什么单独一层网关

`core` 只会 `yield` 事件，不会认识浏览器。这一层做三件事：

1. **翻译**：`AgentEvent` dataclass → JSON 帧（`event_payload`）；
2. **搬运**：阻塞生成器放进工作线程，经 `queue.Queue` 变成 SSE（顺带拿到心跳能力）；
3. **兜底**：单轮锁（409）、工具结果截断、静态资源回落页。

业务逻辑一行都不在这里——所以 Web 与 TUI 的行为天然一致。

## 启动

```bash
cd ../frontend && npm install && npm run build && cd ..
python run.py --ui web                 # http://127.0.0.1:8760
```

配置见 `config.ini` 的 `[web]` 段（host / port / open_browser / assets_dir / heartbeat_seconds）。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bootstrap` | 会话 + 工具 + MCP + 用量 + 模型 |
| POST | `/api/chat` | SSE 事件流 |
| POST | `/api/cancel` | 中止本轮 |
| POST | `/api/session/new` `/save` `/resume` | 会话操作 |
| GET | `/api/sessions` | 历史列表 |
| POST | `/api/compact` | 压缩上下文 |
| GET | `/api/mcp` | MCP 状态（后台加载完轮询） |
| GET | `/api/health` | 探活 |

## 测试

`tests/test_web.py` 用 Starlette `TestClient` 覆盖契约、SSE 时序、会话存档与静态托管，
不联网、不起端口：

```bash
python -m pytest tests/test_web.py -q      # 16 用例
```
