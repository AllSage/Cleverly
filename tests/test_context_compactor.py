"""Tests for context_compactor.py — constants and prompt templates.
Uses mock imports to avoid loading the full app stack."""

import sys
import builtins
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Mock heavy dependencies before importing
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.context_compactor import (
    COMPACT_THRESHOLD,
    SELF_SUMMARY_SYSTEM_PROMPT,
    SUMMARY_MAX_TOKENS,
    trim_for_context,
)
import src.context_compactor as cc

for _stub_name in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'core.models', 'core.database',
]:
    _mod = sys.modules.get(_stub_name)
    if isinstance(_mod, MagicMock):
        sys.modules.pop(_stub_name, None)
        if "." in _stub_name:
            _parent_name, _, _child_name = _stub_name.rpartition(".")
            _parent = sys.modules.get(_parent_name)
            if _parent is not None and hasattr(_parent, _child_name):
                delattr(_parent, _child_name)


class TestCompactThreshold:
    def test_value(self):
        assert COMPACT_THRESHOLD == 0.85

    def test_summary_max_tokens(self):
        assert SUMMARY_MAX_TOKENS == 1024


class TestSelfSummaryPrompt:
    def test_contains_goal_section(self):
        assert "### User Goal" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_what_was_done_section(self):
        assert "### What Was Done" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_current_state_section(self):
        assert "### Current State" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_pending_section(self):
        assert "### Pending / Next Steps" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_key_context_section(self):
        assert "### Key Context" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_count_placeholder(self):
        assert "{count}" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_n_placeholder(self):
        assert "{n}" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_mentions_compactions(self):
        assert "Compactions so far" in SELF_SUMMARY_SYSTEM_PROMPT


class TestTrimForContext:
    def test_returns_original_when_under_budget(self):
        messages = [{"role": "system", "content": "short"}, {"role": "user", "content": "hello"}]

        assert trim_for_context(messages, context_length=10000, reserve_tokens=512) is messages

    def test_keeps_current_large_user_message_by_truncating(self):
        huge = "A" * 20000
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": huge},
        ]

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        user_msgs = [m for m in trimmed if m.get("role") == "user"]
        assert len(user_msgs) == 1
        content = user_msgs[0]["content"]
        assert "pasted message was too large" in content
        assert content.startswith("A")
        assert len(content) < len(huge)

    def test_drops_older_messages_before_latest_user_paste(self):
        huge = "B" * 12000
        messages = [{"role": "system", "content": "You are helpful."}]
        messages.extend({"role": "user", "content": f"old-{i} " + ("x" * 1000)} for i in range(8))
        messages.append({"role": "user", "content": huge})

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        assert trimmed[-1]["role"] == "user"
        assert "pasted message was too large" in trimmed[-1]["content"]
        assert "old-0" not in "\n".join(str(m.get("content", "")) for m in trimmed)

    def test_drops_extra_system_messages_and_sanitizes_tools(self):
        messages = [
            {"role": "system", "content": "preset"},
            {"role": "system", "content": "memory " + ("m" * 5000)},
            {"role": "assistant", "content": "orphan-call", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "dangling"}]},
            {"role": "tool", "content": "orphan output"},
            {"role": "user", "content": "latest"},
        ]

        trimmed = trim_for_context(messages, context_length=900, reserve_tokens=100)

        assert trimmed[0]["content"] == "preset"
        assert all(m.get("content") != messages[1]["content"] for m in trimmed)
        assert {"role": "tool", "content": "tool output"} in trimmed

    def test_truncates_long_system_prompt_before_dropping_conversation(self):
        messages = [
            {"role": "system", "content": "S" * 5000},
            {"role": "user", "content": "hello"},
        ]

        trimmed = trim_for_context(messages, context_length=1200, reserve_tokens=100)

        assert trimmed[0]["role"] == "system"
        assert "System prompt truncated" in trimmed[0]["content"]

    def test_protected_messages_and_extra_system_add_back_branch(self, monkeypatch):
        def fake_estimate(messages):
            return sum(m.get("tokens", 0) for m in messages)

        monkeypatch.setattr(cc, "estimate_tokens", fake_estimate)
        messages = [
            {"role": "system", "content": "preset", "tokens": 10},
            {"role": "system", "content": "small memory", "tokens": 5},
            {"role": "system", "content": "large rag", "tokens": 50},
            {"role": "user", "content": "protected", "tokens": 1, "_protected": True},
            {"role": "user", "content": "latest", "tokens": 40},
        ]

        trimmed = trim_for_context(messages, context_length=100, reserve_tokens=20)

        assert {"role": "system", "content": "small memory", "tokens": 5} in trimmed
        assert {"role": "system", "content": "large rag", "tokens": 50} not in trimmed
        assert {"role": "user", "content": "protected", "tokens": 1, "_protected": True} in trimmed

    def test_protect_recent_branch_drops_oldest_when_many_prior_messages(self):
        messages = [{"role": "system", "content": "s"}]
        messages.extend(
            {"role": "user", "content": f"old-{i} " + ("x" * 1000)}
            for i in range(12)
        )
        messages.append({"role": "user", "content": "current " + ("y" * 5000)})

        trimmed = trim_for_context(messages, context_length=4096, reserve_tokens=512)
        text = "\n".join(str(m.get("content", "")) for m in trimmed)

        assert "old-0" not in text
        assert "current" in text


class TestContextCompactorHelpers:
    def test_sanitize_tool_messages_strips_orphans_and_dangling_calls(self):
        cleaned = cc._sanitize_tool_messages(
            [
                {"role": "tool", "content": "orphan"},
                {"role": "assistant", "content": "call", "tool_calls": [{"id": "1"}]},
                {"role": "tool", "content": "answer"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
                {"role": "assistant", "content": "kept", "tool_calls": [{"id": "3"}]},
            ]
        )

        assert {"role": "tool", "content": "orphan"} not in cleaned
        assert {"role": "tool", "content": "answer"} in cleaned
        assert all(m.get("content") != "" for m in cleaned)
        assert cleaned[-1] == {"role": "assistant", "content": "kept"}

    def test_truncate_helpers_cover_short_budget_string_and_list_content(self):
        assert cc._truncate_text_to_token_budget("long text", 16).startswith("[Current user message omitted")
        assert cc._truncate_text_to_token_budget("short", 500) == "short"

        msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": "kept"},
                {"type": "text", "text": "A" * 20000},
                "raw",
            ],
        }
        truncated = cc._truncate_message_to_token_budget(msg, 200)

        assert truncated is not msg
        assert truncated["content"][0] == {"type": "image_url", "image_url": "kept"}
        assert "pasted message was too large" in truncated["content"][1]["text"]
        assert truncated["content"][2] == "raw"


@pytest.mark.asyncio
async def test_maybe_compact_skips_when_under_threshold_or_too_few_messages(monkeypatch):
    monkeypatch.setattr(cc, "get_context_length", lambda _url, _model: 10000)
    monkeypatch.setattr(cc, "estimate_tokens", lambda _messages: 100)
    messages = [{"role": "user", "content": "hello"}]

    assert await cc.maybe_compact(SimpleNamespace(history=[]), "url", "model", messages) == (
        messages,
        10000,
        False,
    )

    monkeypatch.setattr(cc, "estimate_tokens", lambda _messages: 9000)
    short_messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert await cc.maybe_compact(SimpleNamespace(history=[]), "url", "model", short_messages) == (
        short_messages,
        10000,
        False,
    )


@pytest.mark.asyncio
async def test_maybe_compact_success_updates_history_with_utility_model(monkeypatch):
    monkeypatch.setattr(cc, "get_context_length", lambda _url, _model: 1000)
    monkeypatch.setattr(cc, "estimate_tokens", lambda messages: 900 if len(messages) > 2 else 100)
    monkeypatch.setattr(cc, "resolve_endpoint", lambda _kind: ("utility-url", "utility-model", {"H": "1"}))
    calls = []

    async def fake_llm(url, model, messages, **kwargs):
        calls.append((url, model, messages, kwargs))
        return "dense summary"

    monkeypatch.setattr(cc, "llm_call_async", fake_llm)

    class ChatMessage:
        def __init__(self, role, content, metadata=None):
            self.role = role
            self.content = content
            self.metadata = metadata or {}

    monkeypatch.setattr(cc, "ChatMessage", ChatMessage)
    import core.models as core_models

    monkeypatch.setattr(core_models, "_session_manager", None, raising=False)
    history = [
        ChatMessage("system", "preset"),
        ChatMessage("user", "u1"),
        ChatMessage("assistant", "a1"),
        ChatMessage("user", "u2"),
        ChatMessage("assistant", "a2"),
    ]
    session = SimpleNamespace(id="s1", history=history)
    messages = [
        {"role": "system", "content": "preset"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]

    compacted, context_length, was_compacted = await cc.maybe_compact(
        session,
        "session-url",
        "session-model",
        messages,
        headers={"S": "1"},
    )

    assert was_compacted is True
    assert context_length == 1000
    assert "[Conversation summary" in compacted[1]["content"]
    assert compacted[-2:] == messages[-2:]
    assert calls[0][0] == "utility-url"
    assert calls[0][1] == "utility-model"
    assert calls[0][3]["headers"] == {"H": "1"}
    assert isinstance(session.history[1], ChatMessage)
    assert session.history[1].metadata == {"compacted": True, "summarized_count": 2}


@pytest.mark.asyncio
async def test_maybe_compact_failure_returns_recent_without_marking_compacted(monkeypatch):
    monkeypatch.setattr(cc, "get_context_length", lambda _url, _model: 1000)
    monkeypatch.setattr(cc, "estimate_tokens", lambda _messages: 900)
    monkeypatch.setattr(cc, "resolve_endpoint", lambda _kind: (None, None, None))

    async def fail_llm(*_args, **_kwargs):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(cc, "llm_call_async", fail_llm)
    messages = [
        {"role": "system", "content": "preset"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]

    compacted, _context_length, was_compacted = await cc.maybe_compact(
        SimpleNamespace(history=[]),
        "session-url",
        "session-model",
        messages,
        headers={"S": "1"},
    )

    assert was_compacted is False
    assert compacted == [messages[0]] + messages[3:]


def test_update_session_history_guards_manager_success_and_fallback(monkeypatch):
    assert cc._update_session_history(None, 1, "summary") is None
    assert cc._update_session_history(SimpleNamespace(), 1, "summary") is None

    session = SimpleNamespace(id="s1", history=["sys", "old"])
    assert cc._update_session_history(session, 2, "summary") is None
    assert session.history == ["sys", "old"]

    class ChatMessage:
        def __init__(self, role, content, metadata=None):
            self.role = role
            self.content = content
            self.metadata = metadata or {}

    monkeypatch.setattr(cc, "ChatMessage", ChatMessage)

    class Manager:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def replace_messages(self, session_id, history):
            self.calls.append((session_id, history))
            return self.result

    import core.models as core_models

    manager = Manager(True)
    monkeypatch.setattr(core_models, "_session_manager", manager, raising=False)
    session = SimpleNamespace(id="s1", history=["sys", "old1", "old2", "new"])
    cc._update_session_history(session, 1, "summary", system_msg_count=1)
    assert manager.calls and manager.calls[0][0] == "s1"
    assert session.history == ["sys", "old1", "old2", "new"]

    manager = Manager(False)
    monkeypatch.setattr(core_models, "_session_manager", manager, raising=False)
    session = SimpleNamespace(id="s2", history=["sys", "old1", "old2", "new"])
    cc._update_session_history(session, 1, "summary", system_msg_count=1)
    assert session.history[0] == "sys"
    assert isinstance(session.history[1], ChatMessage)
    assert session.history[2:] == ["old2", "new"]

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "core" and "models" in fromlist:
            raise RuntimeError("core import failed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    session = SimpleNamespace(id="s3", history=["sys", "old1", "new"])
    cc._update_session_history(session, 1, "summary", system_msg_count=1)
    assert session.history[0] == "sys"
    assert isinstance(session.history[1], ChatMessage)
    assert session.history[2:] == ["new"]
