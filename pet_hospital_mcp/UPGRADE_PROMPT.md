# UPGRADE_PROMPT.md —— 下一阶段 MCP 迭代提示词

## 现状（阶段一交付）

`pet_hospital_mcp/` 已交付一个最小可用的 MCP 服务：

- 单一工具 `list_pets`，严格适配 Go REST API `GET /api/v1/pets` 的 14 个查询参数；
- MCP `2026-07-28` 协议、SDK `2.0.0`、`MCPServer`、stateless Streamable HTTP；
- Pydantic v2 + `extra="forbid"` + `strict=True` 输入校验；
- 统一错误结构 `{error:{code,message,details}}`，6 类错误码；
- JSON 日志 + 敏感字段脱敏；
- 教学场景关闭 Host/Origin 校验、不实现认证/CORS；
- 120 项 pytest 全部通过，覆盖 prompt-step1.md 的 7 个场景。

本文件描述下一阶段（阶段二）可能的演进方向，供迭代提示词使用。

---

## 阶段二目标：在保持现有契约不变的前提下，扩展工具集与生产化能力

### 1. 新增工具：`get_pet`

- **后端**：`GET /api/v1/pets/{id}`
- **输入**：`get_pet_input: { id: str }`（必填）
- **输出**：单个 `Pet` 对象（含 `records / charges / totalCost / visitCount`）
- **错误**：复用阶段一的统一错误结构；后端 404 → `BACKEND_API_ERROR`（`details.status_code=404`）
- **测试**：mock transport 返回单条 Pet；404；schema 校验

### 2. 新增工具：`create_pet` / `update_pet` / `delete_pet`

- 这些是写操作，需要考虑：
  - **工具是否标记为「破坏性」**：在 `Tool.annotations` 中设置 `annotations.readonly=false`、`annotations.destructive=true`，让 AI Agent 在调用前向用户确认
  - **请求方法**：`POST / PUT / DELETE`，body 使用 JSON
  - **输入校验**：复用 `extra="forbid"` + `strict=True` 风格
  - **测试**：mock 后端 POST/PUT/DELETE 路径与请求体；409 冲突；422 校验失败

### 3. 新增工具：`list_medical_records` / `add_medical_record`

- 对应 Go REST API 中可能存在的病历/就诊历史端点
- 输入分页参数与阶段一一致

### 4. 引入 `server/discover` 主动响应

阶段一仅依赖 SDK 自动处理 discover。阶段二可显式提供：

- 在 server 端 hook `server/discover` RPC，返回自定义 `supported_versions`、`ttl_ms`、`cache_scope`
- 让客户端可以缓存 discover 结果（降低握手开销）
- 测试：发送 `server/discover` 请求，验证响应包含 `protocolVersion: 2026-07-28` 与 `serverInfo` 元信息

### 5. 引入资源（Resources）与提示（Prompts）

把静态信息暴露为 MCP 资源：

- `pet://species-list` → 返回支持的种类枚举与中文说明
- `pet://status-list` → 返回就诊状态枚举
- `pet://sort-fields` → 返回可用排序字段

把常用查询模式暴露为 MCP Prompts：

- `prompts/recent-critical` → 生成 `list_pets` 的入参模板，过滤出最近 7 天的危重病例
- `prompts/by-doctor` → 接收 doctor 名参数，生成对应 `list_pets` 调用

### 6. 生产化：安全与可观测性

- **启用 DNS-rebinding 保护**：移除 `enable_dns_rebinding_protection=False`，改为按部署域名 allowlist
- **认证**：在 `mcp.streamable_http_app` 上挂载 Bearer Auth 中间件（教学场景可选）
- **CORS**：浏览器客户端场景下添加 `CORSMiddleware`
- **结构化日志增强**：增加 `request_id`、`trace_id`（OpenTelemetry）；通过 SDK 的 `_otel.py` 钩子接入
- **指标**：`/metrics` 暴露 Prometheus 格式（tool 调用次数、duration、错误率）

### 7. 后端连接池与限流

- 当前 `PetHospitalClient` 每次构造一个 `httpx.AsyncClient`；阶段二可改为共享 client + 连接池
- 加入并发上限（信号量）防止后端被打挂
- 加入熔断器：连续 N 次 `BACKEND_UNAVAILABLE` 后短路返回 `BACKEND_UNAVAILABLE`，避免雪崩

### 8. 测试增强

- **状态共享测试**：跨请求验证 session-less 行为（同一 client 连续调用 list_pets 多次，后端 mock 计数）
- **并发测试**：多个 client 并发调用，验证后端重试不会产生副作用
- **协议兼容性测试**：发送旧版 `initialize` 请求，验证服务端不响应（stateless 不实现旧握手）

### 9. 文档与示例

- 在 `examples/` 下提供完整客户端脚本：
  - `examples/cli_client.py`：命令行查询宠物档案
  - `examples/langchain_agent.py`：通过 LangChain 的 MCP adapter 调用
- 在 README 中补充生产部署指南（uvicorn workers、反代、TLS）

---

## 给迭代提示词的关键约束（请保留）

1. **不破坏阶段一契约**：
   - `list_pets` 的输入/输出/错误结构保持不变
   - 协议版本 `2026-07-28`、SDK `2.0.0`、`MCPServer`、stateless Streamable HTTP 保持不变
   - 错误码集合只增不减（不删除现有 6 类）
2. **每个新工具必须有对应的 pytest 套件**，覆盖：
   - 正常调用 + 参数转发
   - 输入校验失败
   - 后端 4xx/5xx
   - 超时/连接异常
   - 后端响应 schema 不符
   - 工具注册、名称、JSON Schema
3. **写操作工具必须标注 `annotations`**（`readonly=false` + `destructive=true`）
4. **不使用 `mcp.server.fastmcp.FastMCP`**（即便 SDK 把它作为别名，导入路径必须是 `mcp.server.MCPServer`）
5. **测试运行命令保持** `cd pet_hospital_mcp; pytest -q`，且全部通过
6. **教学场景默认关闭 Host/Origin 校验**；生产化阶段需在 README 中给出明确开关说明

---

## 期望产出

- 在 `pet_hospital_mcp/src/pet_hospital_mcp/tools/` 下新增 `get_pet.py / create_pet.py / update_pet.py / delete_pet.py / list_medical_records.py / add_medical_record.py`，并在 `register_all` 中注册
- 在 `pet_hospital_mcp/src/pet_hospital_mcp/resources.py` 中注册资源
- 在 `pet_hospital_mcp/src/pet_hospital_mcp/prompts.py` 中注册 prompts
- 在 `tests/` 下为每个新工具/资源/prompt 增加测试文件
- 更新 README 与本 UPGRADE_PROMPT.md 的「现状」段
- 运行 `cd pet_hospital_mcp; pytest -q` 全绿
