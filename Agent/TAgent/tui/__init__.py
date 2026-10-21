# -*- coding: utf-8 -*-
"""TUI 前端（Textual 终端界面）。

目录约定（与 core/ 平级，未来可照抄此结构新增 web/ desktop/ mobile/ frontend/）：::

    tui/
    ├── frontend.py   实现 core.ports.frontend.Frontend —— 唯一被 core 感知的入口
    ├── app.py        Textual App（纯界面，不含业务逻辑）
    ├── bridge.py     事件桥接：core 事件 → 界面动作（防腐层）
    ├── styles.tcss   样式
    └── widgets/      可复用界面组件

约束：
    本目录**不允许**出现任何业务逻辑（文件操作、LLM 调用、配置解析），
    一切能力都通过 `FrontendContext` 注入的 core 对象获得。
"""

__all__ = []
