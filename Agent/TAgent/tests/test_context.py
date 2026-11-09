# -*- coding: utf-8 -*-
"""上下文压缩 / 归档 / 历史检索的测试。

分层覆盖，全部离线可跑（LLM 用脚本化假客户端）：

    1. 领域纯函数：归档编号、Token 估算、用量百分比、序列化、检索匹配；
    2. 归档存储：jsonl 落盘、编号防碰撞、坏行容错；
    3. 压缩服务：归档失败/摘要失败时**会话保持原样**，成功后替换历史；
    4. Agent 集成：usage 事件（优先 API 值）、阈值判断、压缩请求不带工具；
    5. recallContext 工具：参数校验与返回渲染。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Iterator

from core.config.models import AgentSettings, LLMSettings
from core.domain.context import (
    DEFAULT_CONTEXT_WINDOW,
    ArchiveRecord,
    ContextUsage,
    archive_footer,
    clamp_summary,
    estimate_history_tokens,
    estimate_text_tokens,
    format_tokens,
    generate_archive_id,
    is_over_threshold,
    render_records_for_llm,
    search_records,
    serialize_messages,
    split_keywords,
    usage_tokens_from_api,
    with_collision_suffix,
)
from core.domain.events import ContextUsageUpdated, TurnCompleted
from core.domain.messages import ChatMessage, ToolCall
from core.errors import ContextArchiveError, ContextCompactError
from core.infrastructure.context.local_files import LocalContextArchiveStore
from core.infrastructure.llm.mock import ScriptedLLMClient
from core.ports.llm import LLMChunk, LLMClient, LLMRequest
from core.services.agent import AgentService
from core.services.context import ContextService
from core.services.session import Session
from core.tools.builtin_context import build_context_tools
from core.tools.registry import InMemoryToolRegistry


def _settings(**overrides) -> AgentSettings:
    base = dict(
        llm=LLMSettings(base_url="http://x", api_key="k", model="m"),
        enable_system_prompt=True,
    )
    base.update(overrides)
    return AgentSettings(**base)


class _FailingLLM(LLMClient):
    """始终失败的 LLM（用于验证「压缩失败不动会话」）。"""

    def stream(self, request: LLMRequest) -> Iterator[LLMChunk]:  # pragma: no cover
        raise RuntimeError("不该走到 stream")
        yield LLMChunk()  # noqa: unreachable

    def complete_turn(self, request: LLMRequest):
        from core.errors import LLMConnectionError

        raise LLMConnectionError("网络断了")


class _BrokenStore(LocalContextArchiveStore):
    """写入必失败的归档存储。"""

    def save(self, archive_id, records, meta=None) -> str:
        raise ContextArchiveError("磁盘满了")


# ======================================================================
# 1. 领域纯函数
# ======================================================================
class ArchiveIdTest(unittest.TestCase):
    def test_format_is_date_time(self) -> None:
        import time as _time

        moment = _time.mktime((2026, 10, 22, 15, 30, 0, 0, 0, -1))
        self.assertEqual(generate_archive_id(moment), "20261022-153000")

    def test_collision_suffix(self) -> None:
        taken = {"20261022-153000", "20261022-153000-1"}
        self.assertEqual(
            with_collision_suffix("20261022-153000", lambda x: x in taken),
            "20261022-153000-2")
        self.assertEqual(
            with_collision_suffix("20261022-160000", lambda x: x in taken),
            "20261022-160000")


class TokenEstimateTest(unittest.TestCase):
    def test_empty_text(self) -> None:
        self.assertEqual(estimate_text_tokens(""), 0)

    def test_chinese_counts_roughly_one_per_char(self) -> None:
        self.assertEqual(estimate_text_tokens("你好世界"), 4)
        self.assertGreaterEqual(estimate_text_tokens("中文测试内容"), 4)

    def test_english_is_cheaper(self) -> None:
        text = "abcdefgh"
        self.assertEqual(estimate_text_tokens(text), len(text) // 4)

    def test_message_includes_tool_call_arguments(self) -> None:
        message = ChatMessage.assistant(
            None, [ToolCall(id="1", name="readFile",
                            arguments_json='{"path": "/tmp/a.txt"}')])
        self.assertGreater(estimate_history_tokens([message]), 4)

    def test_usage_from_api_prefers_prompt_plus_completion(self) -> None:
        self.assertEqual(
            usage_tokens_from_api({"prompt_tokens": 1200, "completion_tokens": 300}),
            1500)

    def test_usage_from_api_returns_none_without_numbers(self) -> None:
        self.assertIsNone(usage_tokens_from_api(None))
        self.assertIsNone(usage_tokens_from_api({}))
        self.assertIsNone(usage_tokens_from_api({"prompt_tokens": "abc"}))


class ContextUsageTest(unittest.TestCase):
    def test_percent(self) -> None:
        usage = ContextUsage(used_tokens=64000, window_tokens=128000)
        self.assertEqual(usage.percent, 50)

    def test_percent_is_capped(self) -> None:
        usage = ContextUsage(used_tokens=500, window_tokens=100)
        self.assertEqual(usage.percent, 100)

    def test_threshold_boundary(self) -> None:
        self.assertFalse(is_over_threshold(
            ContextUsage(used_tokens=799, window_tokens=1000), 0.8))
        self.assertTrue(is_over_threshold(
            ContextUsage(used_tokens=800, window_tokens=1000), 0.8))

    def test_default_window_flag_in_status(self) -> None:
        usage = ContextUsage(used_tokens=12288, window_tokens=DEFAULT_CONTEXT_WINDOW,
                             window_is_default=True)
        text = usage.format_status()
        self.assertIn("12k", text)
        self.assertIn("128k（默认）", text)
        self.assertIn("9%", text)

    def test_format_tokens(self) -> None:
        self.assertEqual(format_tokens(512), "512")
        self.assertEqual(format_tokens(131072), "128k")


class SerializeTest(unittest.TestCase):
    def test_system_message_is_excluded(self) -> None:
        messages = [
            ChatMessage.system("sys"),
            ChatMessage.user("hi"),
            ChatMessage.assistant("hello"),
        ]
        records = serialize_messages(messages)
        self.assertEqual([r.role for r in records], ["user", "assistant"])
        self.assertEqual([r.seq for r in records], [1, 2])

    def test_tool_call_and_result_are_preserved(self) -> None:
        messages = [
            ChatMessage.user("read it"),
            ChatMessage.assistant(None, [ToolCall(id="c1", name="readFile",
                                                  arguments_json='{"path":"a"}')]),
            ChatMessage(role=__import__("core.domain.messages", fromlist=["Role"]).Role.TOOL,
                        content="文件内容", tool_call_id="c1"),
        ]
        records = serialize_messages(messages)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[1].tool_calls[0]["name"], "readFile")
        self.assertEqual(records[1].tool_calls[0]["arguments"], '{"path":"a"}')
        self.assertEqual(records[2].tool_call_id, "c1")
        self.assertEqual(records[2].content, "文件内容")

    def test_roundtrip_through_json(self) -> None:
        original = ArchiveRecord(seq=3, role="assistant", content="文本",
                                 tool_calls=({"id": "1", "name": "x", "arguments": "{}"},))
        restored = ArchiveRecord.from_json(json.loads(json.dumps(original.to_json())))
        self.assertEqual(restored, original)

    def test_render_for_llm_mentions_role_and_tool(self) -> None:
        records = serialize_messages([
            ChatMessage.user("问题"),
            ChatMessage.assistant(None, [ToolCall(id="1", name="getDate",
                                                  arguments_json="{}")]),
        ])
        text = render_records_for_llm(records)
        self.assertIn("[1] user", text)
        self.assertIn("调用工具 getDate", text)


class SearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            ArchiveRecord(seq=1, role="user", content="请修改 core/services/context.py"),
            ArchiveRecord(seq=2, role="assistant", content="好的，已修改 context.py 的阈值"),
            ArchiveRecord(seq=3, role="user", content="换个话题：今天天气如何"),
        ]

    def test_split_keywords(self) -> None:
        self.assertEqual(split_keywords("Path Token"), ["path", "token"])
        self.assertEqual(split_keywords("单个词"), ["单个词"])
        self.assertEqual(split_keywords("   "), [])

    def test_single_keyword(self) -> None:
        hits = search_records(self.records, ["context.py"], "A")
        self.assertEqual([h.seq for h in hits], [1, 2])
        self.assertTrue(all(h.archive_id == "A" for h in hits))

    def test_multiple_keywords_require_all(self) -> None:
        hits = search_records(self.records, ["context.py", "阈值"], "A")
        self.assertEqual([h.seq for h in hits], [2])

    def test_no_hit(self) -> None:
        self.assertEqual(search_records(self.records, ["不存在"], "A"), [])

    def test_search_is_case_insensitive(self) -> None:
        records = [ArchiveRecord(seq=1, role="user", content="ReadFile 工具")]
        self.assertEqual(len(search_records(records, ["readfile"], "A")), 1)

    def test_snippet_is_truncated(self) -> None:
        long_text = "前缀" * 500 + "关键词" + "后缀" * 500
        records = [ArchiveRecord(seq=1, role="user", content=long_text)]
        hit = search_records(records, ["关键词"], "A")[0]
        self.assertLess(len(hit.snippet), len(long_text))
        self.assertIn("关键词", hit.snippet)


class SummaryTest(unittest.TestCase):
    def test_footer_contains_id_and_time(self) -> None:
        import time as _time

        moment = _time.mktime((2026, 10, 22, 15, 30, 0, 0, 0, -1))
        text = archive_footer("20261022-153000", moment)
        self.assertIn("2026-10-22 15:30:00", text)
        self.assertIn("20261022-153000", text)
        self.assertIn("recallContext", text)

    def test_clamp_summary(self) -> None:
        short = "短摘要"
        self.assertEqual(clamp_summary(short), (short, False))
        long_summary = "字" * 20000
        clamped, truncated = clamp_summary(long_summary)
        self.assertTrue(truncated)
        self.assertLess(len(clamped), len(long_summary))
        self.assertIn("已截断", clamped)


# ======================================================================
# 2. 归档存储
# ======================================================================
class ArchiveStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-archive-")
        self.store = LocalContextArchiveStore(project_root=self.tmp)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saves_under_agents_context(self) -> None:
        path = self.store.save("20261022-153000", [ArchiveRecord(1, "user", "hi")])
        self.assertTrue(os.path.isfile(path))
        self.assertIn(os.path.join(".agents", "context"), path)

    def test_meta_line_and_records(self) -> None:
        path = self.store.save("20261022-153000",
                               [ArchiveRecord(1, "user", "hi"),
                                ArchiveRecord(2, "assistant", "hello")])
        with open(path, encoding="utf-8") as stream:
            lines = [json.loads(line) for line in stream if line.strip()]
        self.assertEqual(lines[0]["type"], "meta")
        self.assertEqual(lines[0]["message_count"], 2)
        self.assertEqual(lines[1]["seq"], 1)
        self.assertEqual(lines[2]["role"], "assistant")

    def test_collision_appends_suffix(self) -> None:
        first = self.store.save("20261022-153000", [ArchiveRecord(1, "user", "a")])
        second = self.store.save("20261022-153000", [ArchiveRecord(1, "user", "b")])
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("20261022-153000-1.jsonl"))

    def test_list_ids_newest_first(self) -> None:
        self.store.save("20261022-153000", [ArchiveRecord(1, "user", "a")])
        self.store.save("20261023-090000", [ArchiveRecord(1, "user", "b")])
        self.assertEqual(self.store.list_ids(),
                         ["20261023-090000", "20261022-153000"])

    def test_empty_records_rejected(self) -> None:
        with self.assertRaises(ContextArchiveError):
            self.store.save("20261022-153000", [])

    def test_load_skips_broken_lines(self) -> None:
        path = self.store.save("20261022-153000", [ArchiveRecord(1, "user", "ok")])
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("{这不是 json}\n")
        records = self.store.load("20261022-153000")
        self.assertEqual(len(records), 1)

    def test_load_missing_id_raises(self) -> None:
        with self.assertRaises(ContextArchiveError):
            self.store.load("20990101-000000")

    def test_search_across_all_archives(self) -> None:
        self.store.save("20261022-153000", [ArchiveRecord(1, "user", "第一份 context 记录")])
        self.store.save("20261023-090000", [ArchiveRecord(1, "user", "第二份 context 记录")])
        hits = self.store.search(["context"])
        self.assertEqual(len(hits), 2)
        # 最新归档排在前面
        self.assertEqual(hits[0].archive_id, "20261023-090000")

    def test_search_limited_to_one_archive(self) -> None:
        self.store.save("20261022-153000", [ArchiveRecord(1, "user", "命中")])
        self.store.save("20261023-090000", [ArchiveRecord(1, "user", "命中")])
        hits = self.store.search(["命中"], "20261022-153000")
        self.assertEqual({h.archive_id for h in hits}, {"20261022-153000"})


# ======================================================================
# 3. 压缩服务
# ======================================================================
class CompactServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-compact-")
        self.store = LocalContextArchiveStore(project_root=self.tmp)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _session(self, turns: int = 2) -> Session:
        session = Session(system_prompt="你是助手")
        for index in range(turns):
            session.add_user("用户问题 %d" % index)
            session.add_assistant("助手回答 %d" % index)
        return session

    def test_usage_prefers_api_values(self) -> None:
        service = ContextService(self.store, ScriptedLLMClient([]), _settings())
        session = self._session(1)
        usage = service.usage(session, [], api_usage={"prompt_tokens": 900,
                                                    "completion_tokens": 100})
        self.assertEqual(usage.used_tokens, 1000)
        self.assertTrue(usage.from_api)
        self.assertTrue(usage.window_is_default)

    def test_usage_falls_back_to_estimate(self) -> None:
        service = ContextService(self.store, ScriptedLLMClient([]), _settings())
        session = self._session(1)
        usage = service.usage(session, [])
        self.assertFalse(usage.from_api)
        self.assertGreater(usage.used_tokens, 0)

    def test_configured_window_is_not_default(self) -> None:
        service = ContextService(self.store, ScriptedLLMClient([]),
                                 _settings(context_window=64000))
        self.assertEqual(service.window_tokens, 64000)
        self.assertFalse(service.window_is_default)

    def test_compaction_replaces_history_but_keeps_system(self) -> None:
        llm = ScriptedLLMClient([{"text": "## 摘要\n关键信息：context.py"}])
        service = ContextService(self.store, llm, _settings())
        session = self._session(2)

        result = service.compact(session, [])

        self.assertTrue(os.path.isfile(result.archive_path))
        self.assertEqual(result.message_count, 4)
        self.assertEqual(len(session.messages), 2)          # system + 摘要
        self.assertEqual(session.messages[0].role.value, "system")
        self.assertIn("## 摘要", session.messages[1].content or "")
        self.assertIn(result.archive_id, session.messages[1].content or "")
        self.assertIn("recallContext", session.messages[1].content or "")

    def test_long_history_shrinks_after_compaction(self) -> None:
        """长会话压缩后必须明显变小（短会话可能因归档说明而略增，属正常）。"""
        llm = ScriptedLLMClient([{"text": "## 摘要\n要点若干"}])
        service = ContextService(self.store, llm, _settings())
        session = Session(system_prompt="你是助手")
        for index in range(20):
            session.add_user("第 %d 个问题：%s" % (index, "细节描述" * 40))
            session.add_assistant("第 %d 个回答：%s" % (index, "实现说明" * 40))

        result = service.compact(session, [])

        self.assertGreater(result.tokens_before, result.tokens_after * 3)
        self.assertLess(estimate_history_tokens(session.messages), result.tokens_before)

    def test_compaction_request_has_no_tools(self) -> None:
        llm = ScriptedLLMClient([{"text": "摘要"}])
        service = ContextService(self.store, llm, _settings())
        service.compact(self._session(1), [{"type": "function"}])

        request = llm.requests[-1]
        self.assertEqual(request.tool_specs, [])            # 压缩期间禁止工具
        self.assertEqual(len(request.messages), 1)
        self.assertIn("上下文管理助手", request.messages[0].content or "")
        self.assertIn("用户问题 0", request.messages[0].content or "")

    def test_archive_failure_keeps_session(self) -> None:
        service = ContextService(_BrokenStore(project_root=self.tmp),
                                 ScriptedLLMClient([{"text": "摘要"}]),
                                 _settings())
        session = self._session(1)
        before = list(session.messages)
        with self.assertRaises(ContextArchiveError):
            service.compact(session, [])
        self.assertEqual(session.messages, before)
        self.assertEqual(len(session.messages), 3)

    def test_llm_failure_keeps_session(self) -> None:
        service = ContextService(self.store, _FailingLLM(), _settings())
        session = self._session(1)
        before = list(session.messages)
        with self.assertRaises(ContextCompactError) as ctx:
            service.compact(session, [])
        self.assertIn("上下文仍完整保留", ctx.exception.message)
        self.assertEqual(session.messages, before)

    def test_empty_summary_is_rejected(self) -> None:
        service = ContextService(self.store, ScriptedLLMClient([{"text": "   "}]),
                                 _settings())
        session = self._session(1)
        before = list(session.messages)
        with self.assertRaises(ContextCompactError):
            service.compact(session, [])
        self.assertEqual(session.messages, before)

    def test_empty_session_is_rejected(self) -> None:
        service = ContextService(self.store, ScriptedLLMClient([{"text": "x"}]),
                                 _settings())
        with self.assertRaises(ContextCompactError):
            service.compact(Session(), [])

    def test_second_compaction_uses_new_id(self) -> None:
        llm = ScriptedLLMClient([{"text": "摘要一"}, {"text": "摘要二"}])
        service = ContextService(self.store, llm, _settings())
        session = self._session(1)

        first = service.compact(session, [])
        session.add_user("新问题")
        session.add_assistant("新回答")
        second = service.compact(session, [])

        self.assertNotEqual(first.archive_id, second.archive_id)
        self.assertEqual(len(self.store.list_ids()), 2)

    def test_summary_is_clamped(self) -> None:
        llm = ScriptedLLMClient([{"text": "字" * 30000}])
        service = ContextService(self.store, llm, _settings())
        result = service.compact(self._session(1), [])
        self.assertTrue(result.summary_truncated)


# ======================================================================
# 4. Agent 集成
# ======================================================================
class AgentIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-agent-")
        self.store = LocalContextArchiveStore(project_root=self.tmp)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _agent(self, script, **overrides):
        llm = ScriptedLLMClient(script)
        settings = _settings(**overrides)
        registry = InMemoryToolRegistry()
        context = ContextService(self.store, llm, settings)
        return AgentService(llm=llm, tools=registry, settings=settings,
                            context=context), context

    def test_usage_event_is_yielded_after_response(self) -> None:
        agent, _ = self._agent([{"text": "回答", "usage": {"prompt_tokens": 2000,
                                                          "completion_tokens": 200}}])
        session = agent.settings and Session(system_prompt="sys")
        events = list(agent.stream_turn(session, "你好"))
        usages = [e for e in events if isinstance(e, ContextUsageUpdated)]
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0].used_tokens, 2200)
        self.assertTrue(usages[0].from_api)
        self.assertEqual(usages[0].window_tokens, DEFAULT_CONTEXT_WINDOW)
        self.assertTrue(usages[0].window_is_default)
        self.assertFalse(usages[0].warning)

    def test_warning_when_threshold_reached(self) -> None:
        agent, _ = self._agent(
            [{"text": "回答", "usage": {"prompt_tokens": 100000,
                                        "completion_tokens": 20000}}],
            context_window=100000)
        session = Session(system_prompt="sys")
        usages = [e for e in agent.stream_turn(session, "你好")
                  if isinstance(e, ContextUsageUpdated)]
        self.assertTrue(usages[-1].warning)
        self.assertGreaterEqual(usages[-1].percent, 80)

    def test_compacted_session_can_continue(self) -> None:
        """压缩后紧接着再问一轮，历史里只有摘要（不会带上旧消息）。"""
        agent, context = self._agent([
            {"text": "第一轮回答"},
            {"text": "## 摘要\n用户在做上下文压缩实验"},
            {"text": "第三轮回答"},
        ])
        session = Session(system_prompt="sys")
        list(agent.stream_turn(session, "第一轮问题"))
        context.compact(session, agent.tool_specs())

        events = list(agent.stream_turn(session, "压缩后的问题"))
        self.assertTrue(any(isinstance(e, TurnCompleted) for e in events))

        request = agent._llm.requests[-1]  # 测试内省
        contents = " ".join(m.content or "" for m in request.messages)
        self.assertIn("摘要", contents)
        self.assertNotIn("第一轮问题", contents)


# ======================================================================
# 5. recallContext 工具
# ======================================================================
class RecallToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tagent-recall-")
        self.store = LocalContextArchiveStore(project_root=self.tmp)
        self.store.save("20261022-153000", [
            ArchiveRecord(1, "user", "请检查 core/services/context.py 的阈值"),
            ArchiveRecord(2, "assistant", "阈值默认是 0.8"),
        ])
        self.service = ContextService(self.store, ScriptedLLMClient([]), _settings())
        self.tool = {t.spec.name: t for t in build_context_tools(self.service)}["recallContext"]

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_requires_text(self) -> None:
        outcome = self.tool.invoke({})
        self.assertFalse(outcome.ok)
        self.assertIn("text", outcome.content)

    def test_hit_returns_archive_id_and_position(self) -> None:
        outcome = self.tool.invoke({"text": "context.py"})
        self.assertTrue(outcome.ok)
        self.assertIn("20261022-153000", outcome.content)
        self.assertIn("第 1 条", outcome.content)
        self.assertIn("context.py", outcome.content)

    def test_scope_by_id(self) -> None:
        outcome = self.tool.invoke({"text": "阈值", "id": "20261022-153000"})
        self.assertTrue(outcome.ok)
        self.assertIn("第 2 条", outcome.content)

    def test_missing_id_reports_clearly(self) -> None:
        outcome = self.tool.invoke({"text": "阈值", "id": "20990101-000000"})
        self.assertFalse(outcome.ok)
        self.assertIn("20990101-000000", outcome.content)

    def test_no_hit_is_success_with_hint(self) -> None:
        outcome = self.tool.invoke({"text": "完全不存在的词"})
        self.assertTrue(outcome.ok)
        self.assertIn("没有找到", outcome.content)


if __name__ == "__main__":
    unittest.main()
