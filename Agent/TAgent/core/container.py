# -*- coding: utf-8 -*-
"""组合根（Composition Root）：唯一负责「把对象拼起来」的地方。

所有 `new` 都集中在这里，业务模块之间只依赖抽象（ports），
因此可以在测试里轻松替换成 Mock 实现，也方便将来按配置切换不同后端。

典型用法::

    container = AppContainer.build(config_path="config.ini")
    ctx = container.build_context()
    frontend.run(ctx)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Iterable

from .config.loader import load_settings
from .config.models import AgentSettings
from .config.template import ensure_local_config
from .domain.mcp import McpSnapshot
from .errors import ConfigError
from .infrastructure.cli.local_subprocess import LocalSubprocessRunner
from .infrastructure.context.local_files import LocalContextArchiveStore
from .infrastructure.llm.openai_compat import OpenAICompatibleClient
from .infrastructure.mcp.json_config import JsonMcpConfigLoader
from .infrastructure.mcp.sdk_client import SdkMcpClientFactory
from .infrastructure.search.baidu_qianfan import BaiduQianfanSearchClient
from .infrastructure.sessions.local_files import LocalSessionRepository
from .infrastructure.skills.local_directory import LocalDirectorySkillStore
from .infrastructure.webpage.curl_trafilatura import CurlTrafilaturaFetcher
from .ports.frontend import FrontendContext
from .ports.llm import LLMClient
from .ports.tools import ToolRegistry
from .services.agent import AgentService
from .services.agents_docs import AgentsDocsService
from .services.calendar import CalendarService
from .services.cli import CommandService
from .services.context import ContextService
from .services.filesystem import FileSystemService
from .services.mcp import McpService
from .services.session import Session
from .services.sessions import SessionService
from .services.skills import SkillService
from .services.webfetch import WebFetchService
from .services.websearch import WebSearchService
from .tools.builtin_cli import build_cli_tools
from .tools.builtin_context import build_context_tools
from .tools.builtin_files import build_file_tools
from .tools.builtin_search import build_search_tools
from .tools.builtin_skills import build_skill_tools
from .tools.builtin_system import build_system_tools
from .tools.builtin_webfetch import build_webfetch_tools
from .tools.mcp_tools import build_mcp_tools
from .tools.registry import InMemoryToolRegistry


@dataclass
class AppContainer:
    """应用容器：持有全部单例级依赖。"""

    settings: AgentSettings
    llm: LLMClient
    tools: ToolRegistry
    agent: AgentService
    #: Skill 服务（发现与按需加载；UI 可据此展示技能数量）
    skills: SkillService = field(default_factory=lambda: _empty_skill_service())
    #: MCP 服务（外部工具服务器；未配置时为空实现）
    mcp: McpService = field(default_factory=lambda: McpService(None, None))
    #: 上下文压缩服务（用量监控 / 归档 / 摘要 / 历史检索）
    context: ContextService | None = None
    #: 会话存档服务（保存 / 列表 / 恢复）
    sessions: SessionService | None = None
    #: AGENTS.md 注入服务
    agents_docs: AgentsDocsService | None = None
    #: 实际生效的配置文件路径（UI 状态栏展示用）
    config_source: str = ""
    #: 本次启动是否自动生成了 config.ini（需要提示用户修改后重启）
    config_created: bool = False
    #: 项目根目录（启动时的工作目录）
    project_root: str = ""
    #: MCP 是否已加载完成（异步加载时初值为 False）
    mcp_loaded: bool = False
    _mcp_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def build(cls,
              config_path: str | None = None,
              search_paths: Iterable[str] | None = None,
              llm: LLMClient | None = None,
              overrides: dict[str, object] | None = None,
              defer_mcp: bool = False,
              ensure_config: bool = True,
              project_root: str | None = None) -> "AppContainer":
        """按默认装配方案构建容器。

        Args:
            config_path: 显式配置文件路径。
            search_paths: 自定义搜索路径（测试用）。
            llm: 注入自定义 LLM 实现（测试用）；默认使用 OpenAI 兼容适配器。
            overrides: 运行期参数覆盖（如 `{"max_tool_rounds": 3}`），
                       会在对象装配完成前生效——配置对象是不可变的，装好之后不允许再改。
            defer_mcp: 先不连接 MCP（TUI 用：先进界面，后台线程再加载）。
            ensure_config: 启动目录缺少 config.ini 时自动生成模板。
            project_root: 项目根目录，默认当前工作目录。
        """
        root = os.path.abspath(project_root or os.getcwd())
        created = False
        if ensure_config and not config_path and not os.environ.get("TAGENT_CONFIG"):
            try:
                path, created = ensure_local_config(root)
            except OSError as exc:
                raise ConfigError("无法自动创建 config.ini：%s（%s）" % (root, exc))
            if created:
                config_path = path

        settings, used = load_settings(config_path, search_paths)
        if overrides:
            settings = settings.with_overrides(**overrides)

        filesystem = FileSystemService(max_read_bytes=settings.max_read_bytes)
        calendar = CalendarService()
        websearch = WebSearchService(
            settings.search, BaiduQianfanSearchClient(settings.search))
        webfetch = WebFetchService(CurlTrafilaturaFetcher(
            timeout=settings.webfetch_timeout,
            max_html_bytes=settings.webfetch_max_html_bytes,
            connect_timeout=settings.webfetch_connect_timeout,
        ))
        # 本地命令行执行：当前不做白名单/审批，以运行 TAgent 的用户权限执行
        cli = CommandService(LocalSubprocessRunner(
            timeout=settings.cli_timeout,
            max_stdout_chars=settings.cli_max_stdout_chars,
            max_stderr_chars=settings.cli_max_stderr_chars,
        ))

        registry = InMemoryToolRegistry()
        registry.register_many(build_system_tools(calendar))
        registry.register_many(build_file_tools(filesystem))
        registry.register_many(build_search_tools(websearch))
        registry.register_many(build_webfetch_tools(webfetch))
        registry.register_many(build_cli_tools(cli))

        client = llm or OpenAICompatibleClient(settings.llm)

        # ---- 上下文压缩（用量监控 / 归档 / 摘要 / 历史检索）----------
        context = ContextService(
            LocalContextArchiveStore(
                root=settings.context_dir or None, project_root=root),
            client,
            settings,
        )
        # 必须在 MCP 之前注册：让 recallContext 也参与「本地工具优先」的撞名判定
        registry.register_many(build_context_tools(context))

        # ---- Agent Skills -------------------------------------------
        skills = SkillService(
            LocalDirectorySkillStore(
                root=settings.skills_dir or None,
                project_root=root,
                max_read_bytes=settings.max_read_bytes,
            )
        )
        registry.register_many(build_skill_tools(skills))

        # ---- 会话存档 + AGENTS.md ------------------------------------
        sessions = SessionService(
            LocalSessionRepository(
                root=settings.sessions.directory or None, project_root=root),
            settings.sessions,
        )
        agents_docs = AgentsDocsService(root, settings.agents_doc)

        # ---- MCP（外部工具服务器）------------------------------------
        # 配置文件定位与 Skills 一致：配置文件默认放在启动目录（当前项目）
        mcp_loader = None
        mcp_factory = None
        if settings.mcp_enabled:
            mcp_loader = JsonMcpConfigLoader(
                settings.mcp_config or None,
                project_root=root,
                default_timeout=settings.mcp_timeout,
            )
            mcp_factory = SdkMcpClientFactory(
                connect_timeout=settings.mcp_connect_timeout)
        mcp = McpService(
            mcp_loader, mcp_factory,
            connect_timeout=settings.mcp_connect_timeout,
            max_result_chars=settings.mcp_max_result_chars,
        )
        mcp_loaded = False
        if not defer_mcp:
            # 先注册本地工具，再挂 MCP 工具：撞名时以本地工具为准
            mcp.load(reserved_names=[tool.spec.name for tool in registry.all()])
            _register_mcp_tools(registry, mcp, settings.mcp_tool_description_chars)
            mcp_loaded = True

        # 只在确实有技能时才注入清单，避免向模型宣传用不上的工具
        if settings.enable_system_prompt:
            settings = settings.with_overrides(
                system_prompt=skills.compose_system_prompt(settings.system_prompt))

        client = llm or OpenAICompatibleClient(settings.llm)

        agent = AgentService(llm=client, tools=registry, settings=settings,
                             context=context)

        return cls(settings=settings, llm=client, tools=registry,
                   agent=agent, skills=skills, mcp=mcp, context=context,
                   sessions=sessions, agents_docs=agents_docs,
                   config_source=used, config_created=created,
                   project_root=root, mcp_loaded=mcp_loaded)

    # ------------------------------------------------------------------
    # MCP 异步加载
    # ------------------------------------------------------------------
    def load_mcp(self) -> McpSnapshot:
        """连接 MCP 服务器并注册工具；幂等，可安全地在后台线程调用。

        TUI 启动时会延迟调用本方法：界面先出来，连接结果回来后再刷新边栏。
        """
        with self._mcp_lock:
            if self.mcp_loaded:
                return self.mcp.snapshot()
            self.mcp.load(reserved_names=[tool.spec.name for tool in self.tools.all()])
            _register_mcp_tools(self.tools, self.mcp,
                                self.settings.mcp_tool_description_chars)
            self.mcp_loaded = True
        self.agent.refresh_tools()
        return self.mcp.snapshot()

    # ------------------------------------------------------------------
    # 产出
    # ------------------------------------------------------------------
    def new_session(self) -> Session:
        """创建一个新会话（带系统提示词 + 注入 AGENTS.md）。"""
        if self.sessions is not None:
            session = self.sessions.create(
                system_prompt=(self.settings.system_prompt
                               if self.settings.enable_system_prompt else None),
                history_limit=self.settings.history_limit,
            )
        else:  # pragma: no cover - 正常装配下不会走到
            session = Session(
                system_prompt=(self.settings.system_prompt
                               if self.settings.enable_system_prompt else None),
                history_limit=self.settings.history_limit,
            )
        self.prepare_session(session)
        return session

    def prepare_session(self, session: Session) -> Session:
        """会话首次加载时的固定动作：注入 AGENTS.md 并记事件日志。

        注入的内容会作为一条普通消息留在会话历史里，后续轮次直接复用，
        因此不会重复消耗 token，也保证请求前缀稳定（缓存友好）。
        """
        if self.agents_docs is None or self.sessions is None:
            return session
        doc = self.agents_docs.apply(session)
        if doc is None:
            return session
        self.sessions.record_event(session.id, "agents_doc_injected", {
            "path": doc.path,
            "chars": doc.chars,
            "bytes": doc.size,
            "truncated": doc.truncated,
        })
        return session

    def restart_session(self, session: Session) -> Session:
        """恢复历史会话后补齐首次加载动作（AGENTS.md 已在历史里时不会重复）。"""
        return self.prepare_session(session)

    def build_context(self, initial_prompt: str | None = None) -> FrontendContext:
        """组装交给前端运行的上下文。"""
        snapshot = self.mcp.snapshot()
        notices = list(snapshot.notices)
        if self.config_created:
            notices.insert(
                0,
                "已为你生成配置文件：%s（当前是占位 mock 信息，需要修改后重启）"
                % self.config_source)
        return FrontendContext(
            settings=self.settings,
            agent=self.agent,
            session=self.new_session(),
            tools=self.tools,
            initial_prompt=initial_prompt,
            new_session=self.new_session,
            context=self.context,
            sessions=self.sessions,
            skills=self.skills,
            load_mcp=self.load_mcp,
            prepare_session=self.prepare_session,
            metadata={
                "config_source": self.config_source,
                "config_created": self.config_created,
                # 没放 Skill 的项目不必在状态栏多显示一段
                "skills": self.skills.describe() if self.skills.has_skills else "",
                "agents_doc": (self.agents_docs.describe()
                               if self.agents_docs is not None else ""),
                # 没有配置 MCP 时为空串，状态栏同样不显示
                "mcp": snapshot.summary if self.mcp_loaded else "",
                # 连接失败 / 配置问题：由前端在启动时提示用户
                "mcp_notices": [] if self.mcp_loaded else [],
                "mcp_loading": not self.mcp_loaded,
                "mcp_snapshot": snapshot,
                "sessions": self.sessions.describe() if self.sessions else "",
            },
        )

    def close(self) -> None:
        self.mcp.close()
        self.agent.close()


def _register_mcp_tools(registry: ToolRegistry, mcp: McpService,
                        max_description_chars: int = 1500) -> None:
    """把已发现的 MCP 工具挂到注册表（重名跳过，不影响其它工具）。"""
    for mcp_tool in build_mcp_tools(mcp, max_description_chars):
        try:
            registry.register(mcp_tool)
        except ValueError:
            continue


def _empty_skill_service() -> SkillService:
    """默认的 Skill 服务（未显式装配时使用，指向启动时工作目录）。"""
    return SkillService(LocalDirectorySkillStore())


__all__ = ["AppContainer"]
