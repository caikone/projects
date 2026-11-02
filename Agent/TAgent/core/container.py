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
from dataclasses import dataclass, field
from typing import Iterable

from .config.loader import load_settings
from .config.models import AgentSettings
from .infrastructure.cli.local_subprocess import LocalSubprocessRunner
from .infrastructure.llm.openai_compat import OpenAICompatibleClient
from .infrastructure.search.baidu_qianfan import BaiduQianfanSearchClient
from .infrastructure.skills.local_directory import LocalDirectorySkillStore
from .infrastructure.webpage.curl_trafilatura import CurlTrafilaturaFetcher
from .ports.frontend import FrontendContext
from .ports.llm import LLMClient
from .ports.tools import ToolRegistry
from .services.agent import AgentService
from .services.calendar import CalendarService
from .services.cli import CommandService
from .services.filesystem import FileSystemService
from .services.session import Session
from .services.skills import SkillService
from .services.webfetch import WebFetchService
from .services.websearch import WebSearchService
from .tools.builtin_cli import build_cli_tools
from .tools.builtin_files import build_file_tools
from .tools.builtin_search import build_search_tools
from .tools.builtin_skills import build_skill_tools
from .tools.builtin_system import build_system_tools
from .tools.builtin_webfetch import build_webfetch_tools
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
    #: 实际生效的配置文件路径（UI 状态栏展示用）
    config_source: str = ""

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def build(cls,
              config_path: str | None = None,
              search_paths: Iterable[str] | None = None,
              llm: LLMClient | None = None,
              overrides: dict[str, object] | None = None) -> "AppContainer":
        """按默认装配方案构建容器。

        Args:
            config_path: 显式配置文件路径。
            search_paths: 自定义搜索路径（测试用）。
            llm: 注入自定义 LLM 实现（测试用）；默认使用 OpenAI 兼容适配器。
            overrides: 运行期参数覆盖（如 `{"max_tool_rounds": 3}`），
                       会在对象装配完成前生效——配置对象是不可变的，装好之后不允许再改。
        """
        settings, used = load_settings(config_path, search_paths)
        if overrides:
            settings = settings.with_overrides(**overrides)

        filesystem = FileSystemService(max_read_bytes=settings.max_read_bytes)
        calendar = CalendarService()
        websearch = WebSearchService(
            settings.search, BaiduQianfanSearchClient(settings.search))
        webfetch = WebFetchService(CurlTrafilaturaFetcher())
        # 本地命令行执行：当前不做白名单/审批，以运行 TAgent 的用户权限执行
        cli = CommandService(LocalSubprocessRunner())

        registry = InMemoryToolRegistry()
        registry.register_many(build_system_tools(calendar))
        registry.register_many(build_file_tools(filesystem))
        registry.register_many(build_search_tools(websearch))
        registry.register_many(build_webfetch_tools(webfetch))
        registry.register_many(build_cli_tools(cli))

        # ---- Agent Skills -------------------------------------------
        # 项目根目录 = 程序启动时的工作目录（规范要求）
        project_root = os.getcwd()
        skills = SkillService(
            LocalDirectorySkillStore(
                root=settings.skills_dir or None,
                project_root=project_root,
                max_read_bytes=settings.max_read_bytes,
            )
        )
        registry.register_many(build_skill_tools(skills))
        # 只在确实有技能时才注入清单，避免向模型宣传用不上的工具
        if settings.enable_system_prompt:
            settings = settings.with_overrides(
                system_prompt=skills.compose_system_prompt(settings.system_prompt))

        client = llm or OpenAICompatibleClient(settings.llm)
        agent = AgentService(llm=client, tools=registry, settings=settings)

        return cls(settings=settings, llm=client, tools=registry,
                   agent=agent, skills=skills, config_source=used)

    # ------------------------------------------------------------------
    # 产出
    # ------------------------------------------------------------------
    def new_session(self) -> Session:
        """创建一个新会话（带系统提示词）。"""
        return Session(
            system_prompt=self.settings.system_prompt if self.settings.enable_system_prompt else None,
            history_limit=self.settings.history_limit,
        )

    def build_context(self, initial_prompt: str | None = None) -> FrontendContext:
        """组装交给前端运行的上下文。"""
        return FrontendContext(
            settings=self.settings,
            agent=self.agent,
            session=self.new_session(),
            tools=self.tools,
            initial_prompt=initial_prompt,
            new_session=self.new_session,
            metadata={
                "config_source": self.config_source,
                # 没放 Skill 的项目不必在状态栏多显示一段
                "skills": self.skills.describe() if self.skills.has_skills else "",
            },
        )

    def close(self) -> None:
        self.agent.close()


def _empty_skill_service() -> SkillService:
    """默认的 Skill 服务（未显式装配时使用，指向启动时工作目录）。"""
    return SkillService(LocalDirectorySkillStore())


__all__ = ["AppContainer"]
