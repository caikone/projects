# -*- coding: utf-8 -*-
"""前端注册表：负责发现并实例化所有界面实现。

发现规则（零配置约定优于配置）：
    扫描与 `core/` 平级的目录，凡是可导入的包且含有前端实现模块的，
    都在 `Frontend.__init_subclass__` 里自动登记。
因此新增 `web/` `desktop/` `mobile/` `frontend/` 时**不需要**改任何已有代码。

错误处理原则：
    自动发现是「尽力而为」——某个界面包加载失败不能拖垮其它界面；
    但失败原因必须被**记录**下来，而不是静默吞掉（否则只剩一句
    「未注册的前端：tui」这种无从下手的报错）。诊断信息由
    `load_errors()` 暴露，按需加载时再升级为 `FrontendError`。
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Callable

from .errors import FrontendError
from .ports.frontend import Frontend

#: 前端标识 → 前端类
_REGISTRY: dict[str, type[Frontend]] = {}

#: 前端包名 → 加载失败原因（仅保留首次、最有信息量的那条）
_LOAD_ERRORS: dict[str, str] = {}

#: 扫描时会跳过的目录（核心、测试、缓存等）
_SKIP_DIRS = frozenset({"core", "tests", "__pycache__", ".git", ".workbuddy", "docs"})

#: 前端实现模块的候选名，按顺序尝试导入
_FRONTEND_MODULES = ("frontend", "app")


def register_frontend_class(cls: type[Frontend]) -> type[Frontend]:
    """登记一个前端类（通常由 `Frontend.__init_subclass__` 自动调用）。"""
    name = getattr(cls, "name", "")
    if not name:
        raise ValueError("前端类必须声明 name：%r" % cls)
    _REGISTRY[name] = cls
    _LOAD_ERRORS.pop(name, None)
    return cls


def register_frontend(name: str, factory: Callable[[], Frontend]) -> None:
    """以工厂函数方式登记（供动态装配场景使用）。"""
    _REGISTRY[name] = factory  # type: ignore[assignment]
    _LOAD_ERRORS.pop(name, None)


def available_frontends() -> dict[str, str]:
    """返回 {前端标识: 描述}。"""
    return {
        name: (getattr(cls, "description", "") or "")
        for name, cls in _REGISTRY.items()
    }


def load_errors() -> dict[str, str]:
    """返回 {前端包名: 加载失败原因}，供 `--ui list` 与诊断使用。"""
    return dict(_LOAD_ERRORS)


def get_frontend_class(name: str) -> type[Frontend] | Callable[[], Frontend]:
    """按标识取前端类；未注册时抛出 FrontendError。"""
    ensure_frontend(name)
    return _REGISTRY[name]


def create_frontend(name: str) -> Frontend:
    """实例化前端；加载失败时抛出带修复建议的 `FrontendError`。"""
    ensure_frontend(name)
    entry = _REGISTRY[name]
    return entry() if isinstance(entry, type) else entry()


def project_root() -> str:
    """项目根目录（`core/` 的上级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def discover_frontends(root: str | None = None, verbose: bool = False) -> list[str]:
    """扫描并导入所有潜在的前端包（尽力而为，单个失败不影响其它）。

    Args:
        root: 扫描根目录，默认项目根。
        verbose: 是否打印加载失败的诊断信息。

    Returns:
        成功导入的包名列表（注意：「导入成功」不等于「注册了前端」）。
    """
    root = root or project_root()
    imported: list[str] = []

    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry)
        if entry.startswith(".") or entry in _SKIP_DIRS:
            continue
        if not os.path.isdir(path) or not os.path.isfile(os.path.join(path, "__init__.py")):
            continue

        if _import_package(entry) is None:
            _LOAD_ERRORS.setdefault(entry, "包 %s 无法导入" % entry)
            continue
        imported.append(entry)

        reason = _load_frontend_module(entry)
        if reason is not None:
            _LOAD_ERRORS.setdefault(entry, reason)

    if verbose:
        for package, reason in sorted(_LOAD_ERRORS.items()):
            print("[前端发现] 跳过 %s：%s" % (package, reason))
    return imported


def ensure_frontend(name: str) -> None:
    """确保指定前端已注册；未注册时尝试按需加载，仍失败则抛出 FrontendError。"""
    if name in _REGISTRY:
        return

    root = project_root()
    if not os.path.isdir(os.path.join(root, name)):
        raise FrontendError(
            "未找到界面 %r：项目根目录下没有同名目录 %s。已注册的界面：%s"
            % (name, os.path.join(root, name), _registered_names()),
            name=name,
            reason="界面目录不存在",
        )

    if _import_package(name) is None:
        reason = "包 %s 无法导入" % name
    else:
        reason = _load_frontend_module(name) or (
            "包 %s 已导入但没有登记任何前端：请在 %s/frontend.py 中定义 Frontend 子类"
            "并声明 name = %r" % (name, name, name))
        if name in _REGISTRY:
            return

    _LOAD_ERRORS.setdefault(name, reason)
    hint = _build_hint(reason)
    message = "无法加载界面 %r：%s" % (name, reason)
    if hint:
        message += "\n%s" % hint
    raise FrontendError(message, name=name, reason=reason, hint=hint)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _registered_names() -> str:
    return "、".join(sorted(_REGISTRY)) or "（空）"


def _import_package(name: str):
    """导入顶层包；失败返回 None（原因由调用方按需重建）。"""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _load_frontend_module(package: str) -> str | None:
    """尝试导入 `<package>.frontend` / `<package>.app`。

    Returns:
        None 表示导入成功或该包确实没有这两个模块；
        否则返回失败原因（**依赖缺失必须与模块缺失区分开**）。
    """
    for module_name in _FRONTEND_MODULES:
        target = "%s.%s" % (package, module_name)
        try:
            importlib.import_module(target)
        except ModuleNotFoundError as exc:
            if _is_missing_target(exc, target):
                continue  # 该包没有这个模块，正常：换下一个候选名
            # target 存在，但它 import 的第三方依赖没装 —— 这是真正的故障
            return "导入 %s 时缺少依赖 %r" % (target, getattr(exc, "name", "") or "?")
        except Exception as exc:  # 语法错误 / 依赖版本不兼容等
            return "导入 %s 失败：%s: %s" % (target, type(exc).__name__, exc)
        else:
            return None
    return None


def _is_missing_target(exc: ModuleNotFoundError, target: str) -> bool:
    """判断异常是「target 模块本身不存在」还是「target 的依赖缺失」。

    ``import tui.frontend`` 若 ``tui/frontend.py`` 不存在，``exc.name`` 为
    ``tui.frontend``；若文件存在但里面 ``import textual`` 失败，``exc.name``
    为 ``textual``。两者语义完全不同，必须分开处理。
    """
    missing = getattr(exc, "name", "") or ""
    if not missing:
        return False
    return missing == target or target.startswith(missing + ".")


def _build_hint(reason: str) -> str:
    """按失败原因生成可执行的修复建议。"""
    if "缺少依赖" in reason:
        requirement = os.path.join(project_root(), "requirements.txt")
        lines = [
            "提示：当前解释器 %s 缺少该界面的第三方依赖。" % sys.executable,
            "      安装依赖： \"%s\" -m pip install -r \"%s\"" % (sys.executable, requirement),
        ]
        alternatives = _candidate_interpreters()
        if alternatives:
            lines.append(
                "      或改用项目虚拟环境： \"%s\" %s"
                % (alternatives[0], os.path.join(project_root(), "run.py")))
        return "\n".join(lines)
    if "没有登记任何前端" in reason:
        return (
            "提示：界面包必须暴露一个 Frontend 子类，且声明类属性 name。\n"
            "      参考 tui/frontend.py 中的 TuiFrontend。"
        )
    return ""


def _candidate_interpreters() -> list[str]:
    """项目内置虚拟环境的解释器（存在即优先推荐，避免污染全局环境）。"""
    root = project_root()
    found: list[str] = []
    for env_name in (".venv", "venv", "env"):
        for parts in (("Scripts", "python.exe"), ("bin", "python")):
            candidate = os.path.join(root, env_name, *parts)
            if os.path.isfile(candidate):
                found.append(candidate)
    return found


__all__ = [
    "register_frontend",
    "register_frontend_class",
    "available_frontends",
    "create_frontend",
    "discover_frontends",
    "ensure_frontend",
    "load_errors",
    "project_root",
]
