# -*- coding: utf-8 -*-
"""前端注册表的回归测试。

核心回归点：**依赖缺失必须被诊断出来，不能被当成「这个包没有前端模块」吞掉**。
曾经的 bug：`import tui.frontend` 抛出的 `ModuleNotFoundError(name='textual')`
被 `except ModuleNotFoundError: continue` 静默跳过，用户只看到
「未注册的前端：tui；已注册：（空）」，完全无从下手。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import frontend_registry as registry  # noqa: E402
from core.errors import FrontendError  # noqa: E402


class FrontendRegistryTest(unittest.TestCase):
    """在临时目录里造几个假的界面包，验证发现与诊断逻辑。"""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="tagent_fe_")
        self._path_added = self.root not in sys.path
        if self._path_added:
            sys.path.insert(0, self.root)
        # 依赖缺失的界面包
        self._make_package("brokenui", "import tagent_missing_dep_xyz\n")
        # 没有任何前端类的界面包
        self._make_package("emptyui", "")

    def tearDown(self) -> None:
        registry._LOAD_ERRORS.clear()
        for name in ("brokenui", "emptyui", "okui"):
            sys.modules.pop(name, None)
            sys.modules.pop("%s.frontend" % name, None)
        if self._path_added and self.root in sys.path:
            sys.path.remove(self.root)
        shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------------
    def _make_package(self, name: str, frontend_source: str) -> None:
        package = os.path.join(self.root, name)
        os.makedirs(package, exist_ok=True)
        with open(os.path.join(package, "__init__.py"), "w", encoding="utf-8") as fp:
            fp.write("")
        if frontend_source is None:
            return
        with open(os.path.join(package, "frontend.py"), "w", encoding="utf-8") as fp:
            fp.write(frontend_source)

    # ------------------------------------------------------------------
    def test_missing_dependency_is_reported_not_swallowed(self) -> None:
        errors = {}
        with mock.patch.object(registry, "_LOAD_ERRORS", errors):
            registry.discover_frontends(root=self.root)

        self.assertNotIn("brokenui", registry.available_frontends())
        self.assertIn("brokenui", errors, "依赖缺失的界面包必须留下诊断信息")
        self.assertIn("缺少依赖", errors["brokenui"])
        self.assertIn("tagent_missing_dep_xyz", errors["brokenui"])

    def test_package_without_frontend_class_gets_hint(self) -> None:
        errors = {}
        with mock.patch.object(registry, "_LOAD_ERRORS", errors):
            registry.discover_frontends(root=self.root)
            with mock.patch.object(registry, "project_root", return_value=self.root):
                with self.assertRaises(FrontendError) as ctx:
                    registry.ensure_frontend("emptyui")

        message = str(ctx.exception)
        self.assertIn("没有登记任何前端", message)
        self.assertIn("Frontend", message)

    def test_missing_dependency_raises_actionable_error(self) -> None:
        with mock.patch.object(registry, "project_root", return_value=self.root):
            with self.assertRaises(FrontendError) as ctx:
                registry.ensure_frontend("brokenui")

        exc = ctx.exception
        self.assertEqual(exc.name, "brokenui")
        self.assertIn("缺少依赖", exc.reason)
        self.assertIn("pip install", exc.hint)
        self.assertIn(sys.executable, exc.hint)

    def test_unknown_frontend_lists_available_ones(self) -> None:
        with mock.patch.object(registry, "project_root", return_value=self.root):
            with self.assertRaises(FrontendError) as ctx:
                registry.ensure_frontend("no_such_ui")
        self.assertIn("未找到界面", str(ctx.exception))

    def test_is_missing_target_distinguishes_dependency_from_module(self) -> None:
        dep_error = ModuleNotFoundError("No module named 'textual'")
        dep_error.name = "textual"
        self.assertFalse(registry._is_missing_target(dep_error, "tui.frontend"))

        module_error = ModuleNotFoundError("No module named 'web.frontend'")
        module_error.name = "web.frontend"
        self.assertTrue(registry._is_missing_target(module_error, "web.frontend"))


class RealTuiFrontendTest(unittest.TestCase):
    """真实项目里的 tui 界面：装了 textual 就必须能被发现。"""

    def test_tui_discovered_when_dependency_present(self) -> None:
        try:
            import textual  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("当前解释器未安装 textual")

        registry.discover_frontends()
        self.assertIn("tui", registry.available_frontends())
        self.assertTrue(isinstance(registry.create_frontend("tui"), registry.Frontend))


if __name__ == "__main__":
    unittest.main(verbosity=2)
