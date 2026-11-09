# -*- coding: utf-8 -*-
"""反诈技能包的回归测试：可被发现、文件齐全、打分可复现。

这三件事是「把 Agent 包装成反诈助手」的地基：
    1. `anti-fraud` 能被 `LocalDirectorySkillStore` 发现并注入 System Prompt；
    2. SKILL.md 里引用的每个 reference / asset / script 都真实存在
       （防的是打包漏文件——模型加载到一半发现路径 404 是最难排查的故障）；
    3. `docs/anti-fraud/cases.json` 里的场景能被 `scripts/risk_score.py`
       复现出约定的分数、等级与退出码（评分表与渲染逻辑不脱节）。

注意：这些测试读的是仓库内的真实技能包，不是临时目录——
包装成果本身就是被测对象。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest

from core.infrastructure.skills.local_directory import LocalDirectorySkillStore

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ROOT = os.path.join(PROJECT_ROOT, ".agents", "skills")
SKILL_DIR = os.path.join(SKILL_ROOT, "anti-fraud")
SKILL_ENTRY = os.path.join(SKILL_DIR, "SKILL.md")
SCORE_SCRIPT = os.path.join(SKILL_DIR, "scripts", "risk_score.py")
CASES_PATH = os.path.join(PROJECT_ROOT, "docs", "anti-fraud", "cases.json")

#: SKILL.md 中形如 `references/xxx.md`、`assets/xxx.md`、`scripts/xxx.py` 的引用
_RESOURCE_REF = re.compile(r"`((?:references|assets|scripts)/[^`]+)`")


def _run_score(signals: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCORE_SCRIPT, "--signals", ",".join(signals)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=SKILL_DIR,
    )


def _parse_output(text: str) -> tuple[int, str, str]:
    total = re.search(r"总分：(-?\d+)", text)
    level = re.search(r"等级：(.+?)（(.+?)）", text)
    if not total or not level:
        raise AssertionError("打分输出缺少「总分 / 等级」行：\n%s" % text)
    return int(total.group(1)), level.group(1), level.group(2)


class AntiFraudSkillPackageTest(unittest.TestCase):
    """技能包的完整性与可发现性。"""

    def test_skill_is_discoverable(self):
        catalog = LocalDirectorySkillStore(root=SKILL_ROOT).discover()
        names = [s.name for s in catalog.skills]
        self.assertIn("anti-fraud", names)

        meta = next(s for s in catalog.skills if s.name == "anti-fraud")
        self.assertIn("反诈", meta.description)
        self.assertIn("诈骗", meta.description)

    def test_no_discovery_issues(self):
        catalog = LocalDirectorySkillStore(root=SKILL_ROOT).discover()
        self.assertEqual(list(catalog.issues), [])

    def test_referenced_resources_all_exist(self):
        with open(SKILL_ENTRY, encoding="utf-8") as fh:
            body = fh.read()
        refs = sorted(set(_RESOURCE_REF.findall(body)))
        self.assertGreaterEqual(len(refs), 10, "SKILL.md 的资源索引疑似被截断")

        missing = [r for r in refs if not os.path.isfile(os.path.join(SKILL_DIR, r))]
        self.assertEqual(missing, [], "SKILL.md 引用了不存在的文件：%s" % missing)

    def test_entry_has_valid_frontmatter(self):
        with open(SKILL_ENTRY, encoding="utf-8") as fh:
            body = fh.read()
        self.assertTrue(body.startswith("---"), "SKILL.md 必须以 Frontmatter 开头")
        head = body.split("---")[1]
        self.assertIn("name: anti-fraud", head)
        self.assertIn("description:", head)


class RiskScoreCaseTest(unittest.TestCase):
    """用例集与打分器的一致性。"""

    @classmethod
    def setUpClass(cls):
        with open(CASES_PATH, encoding="utf-8") as fh:
            cls.dataset = json.load(fh)["cases"]

    def test_dataset_is_reasonable_size(self):
        self.assertGreaterEqual(len(self.dataset), 10)
        ids = [c["id"] for c in self.dataset]
        self.assertEqual(len(ids), len(set(ids)), "用例 id 有重复")

    def test_cases_match_expected_output(self):
        for case in self.dataset:
            with self.subTest(case=case["id"]):
                proc = _run_score(case["signals"])
                total, level, verdict = _parse_output(proc.stdout)
                self.assertEqual(total, case["expect_total"], case["scene"])
                self.assertEqual(level, case["expect_level"], case["scene"])
                self.assertEqual(verdict, case["expect_verdict"], case["scene"])
                self.assertEqual(proc.returncode, case["expect_exit"], case["scene"])

    def test_deadly_signal_overrides_low_total(self):
        # S02 单独命中只有 30 分，但属于一票否决项，必须判「是诈骗」。
        proc = _run_score(["S02"])
        total, level, verdict = _parse_output(proc.stdout)
        self.assertEqual((level, verdict), ("极高危", "是诈骗"))
        self.assertGreaterEqual(total, 70)
        self.assertIn("一票否决", proc.stdout)
        self.assertEqual(proc.returncode, 1)

    def test_signal_list_has_eighteen_entries(self):
        proc = subprocess.run(
            [sys.executable, SCORE_SCRIPT, "--list"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=SKILL_DIR,
        )
        self.assertEqual(proc.returncode, 0)
        codes = re.findall(r"^S\d{2}", proc.stdout, flags=re.MULTILINE)
        self.assertEqual(len(codes), 18)
        self.assertIn("S01", proc.stdout)

    def test_unknown_signal_is_rejected(self):
        proc = _run_score(["S99"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("未知信号编号", proc.stderr)


if __name__ == "__main__":
    unittest.main()
