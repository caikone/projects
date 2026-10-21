# desktop/ —— 桌面界面（预留）

> 与 `core/` 平级的第 N 个界面实现。尚未实现，目录仅为占位与契约说明。

落地三步（**不需要改动 core 或 run.py**）：

1. 新建 `desktop/__init__.py` 与 `desktop/frontend.py`；
2. 实现 `Frontend` 子类（可选框架：PySide6 / PyQt / Flet / tkinter）：
   ```python
   from core.ports.frontend import Frontend, FrontendContext

   class DesktopFrontend(Frontend):
       name = "desktop"
       description = "桌面窗口界面"
       requires_tty = False

       def run(self, ctx: FrontendContext) -> int:
           from .window import MainWindow
           MainWindow(ctx).mainloop()
           return 0
   ```
3. `python run.py --ui desktop` 启动。

要点：GUI 框架只能在UI线程更新控件——请把 `stream_turn()` 放到工作线程，
事件经队列回流（可直接复用 `tui/app.py` 中的「线程 + 队列 + 定时泵」模式）。
