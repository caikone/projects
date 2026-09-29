"""AnythingLLM MCP Server (MVP) - MCP 协议 2026-07-28, Streamable HTTP 传输"""
import httpx
from mcp.server.mcpserver import MCPServer

BASE_URL = "http://localhost:3001/api"
API_KEY = "9A87SCW-GE34C8R-PW13JDM-3WSASVG"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

mcp = MCPServer("anythingllm")


@mcp.tool()
def ask_first_workspace(question: str) -> str:
    """向 AnythingLLM 第一个工作区提问，返回 AI 回答"""
    with httpx.Client(timeout=120) as client:
        workspaces = client.get(f"{BASE_URL}/v1/workspaces", headers=HEADERS).json()["workspaces"]
        if not workspaces:
            return "错误：没有任何工作区"
        slug = workspaces[0]["slug"]
        resp = client.post(
            f"{BASE_URL}/v1/workspace/{slug}/chat",
            headers=HEADERS,
            json={"message": question, "mode": "chat"},
        ).json()
    if resp.get("error"):
        return f"错误：{resp['error']}"
    return resp.get("textResponse", "（无回答）")


@mcp.tool()
def list_workspace_files() -> str:
    """列出所有 AnythingLLM 工作区及其包含的文件数量与文件名"""
    with httpx.Client(timeout=120) as client:
        workspaces = client.get(f"{BASE_URL}/v1/workspaces", headers=HEADERS).json()["workspaces"]
        if not workspaces:
            return "错误：没有任何工作区"
        lines = []
        total = 0
        for ws in workspaces:
            detail = client.get(f"{BASE_URL}/v1/workspace/{ws['slug']}", headers=HEADERS).json()
            docs = detail["workspace"][0].get("documents", [])
            total += len(docs)
            lines.append(f"工作区「{ws['name']}」：{len(docs)} 个文件")
            for doc in docs:
                path = doc.get("docpath") or doc.get("filename") or str(doc)
                lines.append(f"  - {path}")
        lines.append(f"总计：{total} 个文件")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
