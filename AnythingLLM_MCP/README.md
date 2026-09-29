# AnythingLLM MCP Server

基于 MCP 协议 2026-07-28 的最小 MCP Server，通过 Streamable HTTP 传输访问本机 AnythingLLM。

提供一个工具 `ask_first_workspace(question)`：向 AnythingLLM 第一个工作区提问，返回 AI 回答。

## 依赖安装

```
pip install -r requirements.txt
```

## 启动 / 停止

启动：

```
python server.py
```

服务监听 `http://127.0.0.1:8000/mcp`。

停止：在运行窗口按 `Ctrl+C`；若是后台进程，按端口杀进程：

```
netstat -ano | findstr :8000
taskkill /PID <进程号> /F
```

## 项目级 MCP 配置

`.trae/mcp.json` 为受保护文件，需在 IDE 中手动添加：

```json
{
  "mcpServers": {
    "anythingllm": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

## 验证

```
python test_client.py
```

预期输出：协议版本 `2026-07-28`、工具列表含 `ask_first_workspace`、以及 AnythingLLM 的回答。

## 说明

- AnythingLLM 地址：`http://localhost:3001/api`，API Key 硬编码在 [server.py](server.py) 中。
- 2026-07-28 协议使用 `server/discover` 握手（非传统 `initialize`）。
