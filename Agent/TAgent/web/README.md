# web/ —— Web 界面（预留）

> 与 `core/` 平级的第 N 个界面实现。尚未实现，目录仅为占位与契约说明。

落地三步（**不需要改动 core 或 run.py**）：

1. 新建 `web/__init__.py` 与 `web/frontend.py`；
2. 在 `frontend.py` 中实现 `Frontend` 子类并声明 `name = "web"`：
   ```python
   from core.ports.frontend import Frontend, FrontendContext

   class WebFrontend(Frontend):
       name = "web"
       description = "Web 界面（FastAPI + SSE）"
       requires_tty = False

       def run(self, ctx: FrontendContext) -> int:
           import uvicorn
           from .server import create_app
           uvicorn.run(create_app(ctx), host="127.0.0.1", port=8000)
           return 0
   ```
3. `python run.py --ui web` 即可启动（`core.frontend_registry` 会自动发现）。

要点：
- 复用 `ctx.agent.stream_turn(session, text)` 的同一条事件流，逐个事件转成 SSE 推给浏览器；
- 浏览器端工程放 `../frontend/`；
- Web 相关的第三方依赖（fastapi / uvicorn）只允许出现在本目录。
