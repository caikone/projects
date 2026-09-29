# pet-hospital-mcp

将 [Go 宠物医院 REST API](../) 的 `GET /api/v1/pets` 暴露给 AI Agent 的 MCP 服务。

- **MCP 协议版本**：`2026-07-28`（stateless core；不实现旧协议的 `initialize` 握手 / `Mcp-Session-Id` / 会话存储）
- **MCP Python SDK**：`mcp==2.0.0`，使用 `mcp.server.MCPServer`（不使用、不导入 `mcp.server.fastmcp.FastMCP`）
- **传输**：Streamable HTTP（`stateless_http=True`，`json_response=True`）
- **工具**：`list_pets` —— 严格适配 `GET /api/v1/pets` 的 14 个查询参数（q / name / ownerName / ownerPhone / species / doctor / disease / status / min / max / sortBy / order / page / pageSize）
- **校验**：Pydantic v2，`extra="forbid"` + `strict=True`，拒绝未知字段、类型错误、NaN、Infinity、`min>max`
- **错误归一化**：所有上游异常统一为 `{error:{code,message,details}}`，错误码为 `BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR / BACKEND_INVALID_RESPONSE / VALIDATION_ERROR / INTERNAL_ERROR`
- **日志**：JSON 行格式，自动脱敏 `ownerPhone / ownerAddr / chipNo`（含 snake_case 写法）
- **教学场景**：不实现认证、权限、CORS 或 Origin 校验；通过 `TransportSecuritySettings(enable_dns_rebinding_protection=False)` 关闭 Host/Origin 校验，仅在 `127.0.0.1` 监听

---

## 目录结构

```
pet_hospital_mcp/
├── pyproject.toml
├── README.md                          # 本文档
├── UPGRADE_PROMPT.md                  # 下一阶段迭代提示词
├── src/pet_hospital_mcp/
│   ├── __init__.py                    # __version__ = "1.0.0"
│   ├── __main__.py                    # python -m pet_hospital_mcp 入口
│   ├── config.py                      # Settings dataclass + 环境变量加载
│   ├── errors.py                      # 统一错误结构 + BackendError
│   ├── logging_config.py              # JSON 格式器 + 敏感字段脱敏
│   ├── rest_client.py                 # httpx 调用 Go REST API + 有限重试
│   ├── server.py                      # MCPServer 装配 + /health + ASGI app
│   └── tools/
│       ├── __init__.py                # register_all
│       └── list_pets.py               # list_pets 工具（输入/输出模型 + ToolError）
└── tests/
    ├── __init__.py
    ├── conftest.py                    # 共享 fixtures + 后端 mock 工厂
    ├── test_input_validation.py       # 场景 2：输入校验失败
    ├── test_tool_success.py           # 场景 1：正常调用 + 参数转发
    ├── test_backend_errors.py         # 场景 3：4xx/5xx + 业务 code!=200
    ├── test_backend_timeout.py        # 场景 4：超时 / 连接异常 + 重试恢复
    ├── test_backend_invalid_response.py  # 场景 5：非 JSON / schema 不符
    ├── test_tool_registration.py     # 场景 6：注册 / 名称 / JSON Schema
    └── test_stateless_http.py         # 场景 7：无状态 HTTP 流程
```

## 安装

```powershell
cd pet_hospital_mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

依赖来自 `pyproject.toml`：

- 运行时：`mcp==2.0.0`、`httpx>=0.27`、`pydantic>=2.12`、`uvicorn>=0.30`
- 测试：`pytest>=8`、`pytest-asyncio>=0.23`、`httpx2>=2.5.0`（`mcp` 传递依赖）

## 运行测试

```powershell
cd pet_hospital_mcp
pytest -q
```

预期输出（120 项全部通过）：

```
120 passed in 1.00s
```

测试覆盖（对应 prompt-step1.md 的 7 个场景）：

| # | 场景 | 测试文件 |
|---|------|----------|
| 1 | 正常调用 + 参数转发 | `test_tool_success.py` |
| 2 | 输入校验失败（species/status/sortBy/order/page/pageSize/min/max/NaN/Infinity/未知字段/类型错误） | `test_input_validation.py` |
| 3 | 后端 4xx/5xx + 业务 code≠200 | `test_backend_errors.py` |
| 4 | 超时 + 连接异常 + 重试恢复 | `test_backend_timeout.py` |
| 5 | 非 JSON / 信封结构非法 / schema 不符 | `test_backend_invalid_response.py` |
| 6 | 工具注册 / 名称 / inputSchema / outputSchema | `test_tool_registration.py` |
| 7 | 无状态 HTTP 流程（/health + tools/list + tools/call + 无 Mcp-Session-Id + protocol 2026-07-28） | `test_stateless_http.py` |

## 启动服务

### 方式 1：直接运行（推荐教学场景）

```powershell
cd pet_hospital_mcp
.\.venv\Scripts\python.exe -m pet_hospital_mcp
```

监听 `http://127.0.0.1:8000/mcp`，健康检查在 `http://127.0.0.1:8000/health`。

### 方式 2：通过 uvicorn 加载 ASGI app

```powershell
.\.venv\Scripts\python.exe -m uvicorn pet_hospital_mcp.server:app --host 127.0.0.1 --port 8000
```

### 方式 3：项目脚本入口

```powershell
.\.venv\Scripts\pet-hospital-mcp.exe
```

> 默认配置指向 `http://127.0.0.1:8080` 的 Go 宠物医院后端。要切换地址，设置环境变量 `PET_HOSPITAL_BASE_URL`。

## 配置

所有配置从环境变量读取（`config.py`）：

| 变量 | 默认 | 含义 |
|------|------|------|
| `MCP_HOST` | `127.0.0.1` | MCP 服务监听地址 |
| `MCP_PORT` | `8000` | MCP 服务监听端口 |
| `MCP_ENDPOINT_PATH` | `/mcp` | MCP 端点路径 |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | 上游 Go 宠物医院 REST API |
| `PET_HOSPITAL_TIMEOUT` | `10.0` | 单次后端调用超时（秒） |
| `PET_HOSPITAL_RETRIES` | `2` | 超时/连接异常时的有限重试次数（共 attempts=retries+1 次） |
| `LOG_LEVEL` | `INFO` | 日志级别 |

## 工具说明：`list_pets`

### 用途

调用上游 Go REST API `GET /api/v1/pets`，返回符合分页结构的宠物档案列表（过滤 + 排序 + 分页）。适合在 AI Agent 需要浏览、筛选或统计宠物档案时使用。

### 输入参数（全部可选）

工具签名 `list_pets(filters: ListPetsInput)` —— 顶层必传 `filters` 参数，其内部 14 个字段全部可选；未提供的字段不转发给后端。

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `q` | string | — | 全文模糊匹配（跨字段） |
| `name` | string | — | 宠物姓名模糊匹配 |
| `ownerName` | string | — | 主人姓名模糊匹配 |
| `ownerPhone` | string | — | 主人电话模糊匹配（日志中自动脱敏） |
| `species` | enum | 犬/猫/兔/鸟/仓鼠/爬宠/其他 | 种类精确匹配 |
| `doctor` | string | — | 主治医生模糊匹配 |
| `disease` | string | — | 疾病模糊匹配 |
| `status` | enum | 待就诊/就诊中/住院中/已康复/慢性病随访 | 就诊状态精确匹配 |
| `min` | number | ≥0、有限数 | 最低总花费 |
| `max` | number | ≥0、有限数 | 最高总花费 |
| `sortBy` | enum | id/name/ownerName/species/doctor/disease/status/totalCost/visitCount/createdAt/updatedAt | 排序字段 |
| `order` | enum | asc/desc | 排序方向 |
| `page` | integer | ≥1 | 页码 |
| `pageSize` | integer | 1–500 | 每页条数 |

约束：
- `min ≤ max`（`min==max` 视为闭区间单点，允许）
- 拒绝 `NaN`、`Infinity`、`-Infinity`
- `extra="forbid"`：未知字段直接拒绝
- `strict=True`：禁止字符串到数字的隐式转换（如 `"1"` 不会被接受为 `page`）

### 输出结构（成功）

```json
{
  "items": [{ "id": "p1", "name": "小黑", "species": "犬", ... }],
  "total": 1,
  "page": 1,
  "pageSize": 20,
  "totalPages": 1,
  "totalCost": 0.0
}
```

字段说明：

| 字段 | 类型 | 含义 |
|------|------|------|
| `items` | array of Pet | 当前页宠物档案列表 |
| `total` | integer | 过滤后总记录数 |
| `page` | integer | 当前页码 |
| `pageSize` | integer | 每页条数 |
| `totalPages` | integer | 总页数 |
| `totalCost` | number | 结果集花费合计 |

`Pet` 模型与 Go `model.Pet` 对齐，含 `id/name/species/breed/gender/ageMonths/color/chipNo/ownerName/ownerPhone/ownerAddr/doctor/disease/status/allergy/note/records/charges/totalCost/visitCount/createdAt/updatedAt`。
`records / charges` 在真实后端响应中可能是 `null` 或数组，模型用 `list | None` 兼容两种表现。

### 错误结构（失败）

工具失败时返回 `is_error=true`，且 `content[0].text` 为如下 JSON（SDK 2.x 在外层包装 `"Error executing tool list_pets: <payload>"` 前缀，客户端解析时应自动定位首个 `{` 起始处）：

```json
{
  "error": {
    "code": "BACKEND_API_ERROR",
    "message": "后端返回 HTTP 500",
    "details": {
      "url": "http://127.0.0.1:8080/api/v1/pets",
      "status_code": 500,
      "body": "..."
    }
  }
}
```

错误码：

| code | 触发条件 |
|------|----------|
| `VALIDATION_ERROR` | 工具输入校验失败（Pydantic ValidationError 在 SDK 层抛出） |
| `BACKEND_TIMEOUT` | 调用 Go REST API 超时（httpx.TimeoutException） |
| `BACKEND_UNAVAILABLE` | 无法连接后端（httpx.ConnectError/NetworkError/TransportError） |
| `BACKEND_API_ERROR` | Go REST API 返回 4xx/5xx，或信封 `code != 200` |
| `BACKEND_INVALID_RESPONSE` | 后端返回非 JSON、信封结构非法、或 data 不符合 ListPetsOutput/Pet 模型 |
| `INTERNAL_ERROR` | 其他未预期异常 |

## 与上游 Go REST API 的契约

- **请求**：`GET /api/v1/pets?<query>`，14 个查询参数名严格对齐（camelCase）
- **响应信封**：`{code, message, data, time}`
- **成功条件**：HTTP 2xx + 信封 `code == 200` + `data` 为对象
- **业务错误**：信封 `code != 200` → `BACKEND_API_ERROR`，消息取自 `message`
- **重试**：仅对 `TimeoutException` 和 `ConnectError/NetworkError/TransportError` 重试，最多 `retries+1` 次；其他 `httpx.HTTPError` 不重试
- **Pet 模型**：与 Go `model.Pet` 对齐；`records/charges` 用 `list | None` 兼容 Go 空切片序列化为 `null` 的情况

## 健康检查

`GET /health`（不经 MCP 协议，由 `@mcp.custom_route` 暴露）：

```json
{
  "status": "healthy",
  "service": "pet-hospital-mcp",
  "mcpEndpoint": "/mcp",
  "protocolVersion": "2026-07-28",
  "sdkVersion": "2.0.0",
  "backend": "http://127.0.0.1:8080",
  "stateless": true
}
```

## 客户端调用示例

```python
import asyncio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import httpx2

async def main():
    # 直接连远端 MCP 服务
    async with Client("http://127.0.0.1:8000/mcp") as c:
        # 协议版本
        print(c.protocol_version)  # 2026-07-28

        # 列出工具
        listing = await c.list_tools()
        print([t.name for t in listing.tools])  # ['list_pets']

        # 调用 list_pets
        result = await c.call_tool("list_pets", {"filters": {"species": "犬", "page": 1}})
        if result.is_error:
            print("error:", result.content[0].text)
        else:
            print("items:", result.structured_content["items"])

asyncio.run(main())
```
