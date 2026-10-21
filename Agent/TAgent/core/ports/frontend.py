# -*- coding: utf-8 -*-
"""前端端口（Port）：所有界面形态（TUI / Web / Desktop / Mobile）的统一契约。

核心层通过 `core.container.AppContainer` 组装完成后，
把 `FrontendContext` 交给某个 `Frontend` 实现，由它接管进程。

新增一种界面 = 在项目根目录新建一个与 `core/` 平级的目录
（如 `web/` / `desktop/` / `mobile/` / `frontend/`），
在里面实现 `Frontend` 子类即可——`core.frontend_registry` 会自动发现并注册，
`run.py --ui <name>` 即可启动，核心层零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, ClassVar

from ..config.models import AgentSettings
from ..ports.tools import ToolRegistry

if TYPE_CHECKING:  # 仅类型标注需要，避免 core.services ↔ core.ports 循环导入
    from ..services.agent import AgentService
    from ..services.session import Session


@dataclass
class FrontendContext:
    """交给前端运行的一切依赖（依赖注入，前端不需要自己造对象）。

    Attributes:
        settings:   全量配置（含 LLM 配置与 Agent 运行参数）。
        agent:      核心对话服务，UI 通过 `agent.stream_turn()` 驱动。
        session:    当前会话（持有完整对话历史）。
        tools:      工具注册表（UI 可据此展示可用工具清单）。
        initial_prompt: 命令行传入的首个提问（可为 None）。
        new_session:  会话工厂（前端需要「开新会话」时调用，避免自己拼装依赖）。
        metadata:   透传给前端的额外信息（如配置来源、启动耗时）。
    """

    settings: AgentSettings
    agent: AgentService
    session: Session
    tools: ToolRegistry
    initial_prompt: str | None = None
    new_session: Callable[[], Session] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class Frontend(ABC):
    """界面实现基类。

    子类只要声明 `name`，就会被 `core.frontend_registry` 自动登记
    （通过 `__init_subclass__` 钩子，无需手写装饰器）。
    """

    #: 前端唯一标识，用于 `run.py --ui tui` 选择
    name: ClassVar[str] = ""
    #: 一行人类可读描述，用于 `--ui list`
    description: ClassVar[str] = ""
    #: 是否需要交互式终端（无 TTY 环境可据此拒绝启动）
    requires_tty: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # 只有声明了 name 的具体前端才登记，抽象中间层会被跳过
        if getattr(cls, "name", ""):
            from ..frontend_registry import register_frontend_class

            register_frontend_class(cls)

    @abstractmethod
    def run(self, ctx: FrontendContext) -> int:
        """接管进程直到用户退出，返回进程退出码。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 可选钩子：子类按需覆盖
    # ------------------------------------------------------------------
    def on_event(self, event: object) -> None:
        """收到核心事件的回调（默认忽略）。

        多数前端会直接遍历 `agent.stream_turn()` 的返回值，
        此钩子主要为「事件驱动型」前端（Web SSE / 桌面端消息队列）预留。
        """

    def shutdown(self) -> None:
        """进程退出前清理资源（默认无操作）。"""


__all__ = ["Frontend", "FrontendContext"]
