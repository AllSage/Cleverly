"""Tests for agent_loop.py — _detect_admin_intent and _compute_final_metrics.
Uses mock imports to avoid loading the full app stack."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

# Mock heavy dependencies before importing
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'src.agent_tools',
    'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.agent_loop import _detect_admin_intent, _compute_final_metrics
from src import agent_loop

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


# ---------------------------------------------------------------------------
# _detect_admin_intent
# ---------------------------------------------------------------------------

class TestDetectAdminIntent:
    """Test admin-intent detection from the last user message."""

    def _msgs(self, text: str):
        """Helper: wrap text in a minimal messages list."""
        return [{"role": "user", "content": text}]

    # --- Should detect admin intent ---

    def test_add_endpoint(self):
        assert _detect_admin_intent(self._msgs("add a new endpoint")) is True

    def test_create_endpoint(self):
        assert _detect_admin_intent(self._msgs("create endpoint for openai")) is True

    def test_manage_sessions(self):
        assert _detect_admin_intent(self._msgs("list all sessions")) is True

    def test_rename_session(self):
        assert _detect_admin_intent(self._msgs("rename this session")) is True

    def test_archive_session(self):
        assert _detect_admin_intent(self._msgs("archive old sessions")) is True

    def test_configure_settings(self):
        assert _detect_admin_intent(self._msgs("configure my settings")) is True

    def test_mcp_server(self):
        assert _detect_admin_intent(self._msgs("add an MCP server")) is True

    def test_api_key(self):
        assert _detect_admin_intent(self._msgs("update the API key")) is True

    def test_list_models(self):
        assert _detect_admin_intent(self._msgs("list models available")) is True

    def test_switch_model(self):
        assert _detect_admin_intent(self._msgs("switch model to gpt-4")) is True

    def test_manage_skills(self):
        assert _detect_admin_intent(self._msgs("show me my skills")) is True

    def test_schedule_task(self):
        assert _detect_admin_intent(self._msgs("schedule a cron task")) is True

    def test_case_insensitive(self):
        assert _detect_admin_intent(self._msgs("MANAGE SESSIONS")) is True

    # --- Should NOT detect admin intent ---

    def test_hello(self):
        assert _detect_admin_intent(self._msgs("hello")) is False

    def test_write_code(self):
        assert _detect_admin_intent(self._msgs("write some python code")) is False

    def test_explain_concept(self):
        assert _detect_admin_intent(self._msgs("explain how transformers work")) is False

    def test_general_question(self):
        assert _detect_admin_intent(self._msgs("what is the capital of France?")) is False

    # --- Edge cases ---

    def test_empty_messages(self):
        assert _detect_admin_intent([]) is False

    def test_no_user_message(self):
        assert _detect_admin_intent([{"role": "assistant", "content": "hi"}]) is False

    def test_multimodal_content(self):
        """Content as a list of blocks (vision messages)."""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "rename this session please"},
        ]}]
        assert _detect_admin_intent(msgs) is True

    def test_multimodal_no_admin(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "describe this image"},
        ]}]
        assert _detect_admin_intent(msgs) is False

    def test_uses_last_user_message(self):
        """Should check only the last user message."""
        msgs = [
            {"role": "user", "content": "rename this session"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "thanks, now just say hello"},
        ]
        assert _detect_admin_intent(msgs) is False


# ---------------------------------------------------------------------------
# _compute_final_metrics
# ---------------------------------------------------------------------------

class TestComputeFinalMetrics:
    """Test metric computation with real and estimated usage."""

    def _base_args(self, **overrides):
        defaults = dict(
            messages=[{"role": "user", "content": "hello world"}],
            full_response="This is a test response.",
            total_duration=2.0,
            time_to_first_token=0.5,
            context_length=8192,
            real_input_tokens=100,
            real_output_tokens=50,
            has_real_usage=True,
            tool_events=[],
            round_texts=[],
            model="test-model",
            last_round_input_tokens=0,
            prep_timings=None,
        )
        defaults.update(overrides)
        return defaults

    def test_real_usage_tokens(self):
        m = _compute_final_metrics(**self._base_args())
        assert m["input_tokens"] == 100
        assert m["output_tokens"] == 50
        assert m["total_tokens"] == 150
        assert m["usage_source"] == "real"

    def test_estimated_usage_tokens(self):
        m = _compute_final_metrics(**self._base_args(
            has_real_usage=False,
            real_input_tokens=0,
            real_output_tokens=0,
        ))
        # Estimated: len("hello world\n") // 4 = 3
        assert m["input_tokens"] == 3
        assert m["usage_source"] == "estimated"

    def test_tps_calculation(self):
        m = _compute_final_metrics(**self._base_args(
            real_output_tokens=100,
            total_duration=2.0,
        ))
        assert m["tokens_per_second"] == 50.0

    def test_tps_zero_duration(self):
        m = _compute_final_metrics(**self._base_args(total_duration=0.0))
        assert m["tokens_per_second"] == 0

    def test_context_percent(self):
        m = _compute_final_metrics(**self._base_args(
            real_input_tokens=4096,
            context_length=8192,
        ))
        assert m["context_percent"] == 50.0

    def test_context_percent_capped_at_100(self):
        m = _compute_final_metrics(**self._base_args(
            real_input_tokens=10000,
            context_length=8192,
        ))
        assert m["context_percent"] == 100.0

    def test_context_percent_zero_context_length(self):
        m = _compute_final_metrics(**self._base_args(context_length=0))
        assert m["context_percent"] == 0

    def test_last_round_input_tokens_used_for_context_pct(self):
        """When last_round_input_tokens > 0, it should be used for context %."""
        m = _compute_final_metrics(**self._base_args(
            real_input_tokens=100,
            last_round_input_tokens=4096,
            context_length=8192,
        ))
        assert m["context_percent"] == 50.0

    def test_response_time(self):
        m = _compute_final_metrics(**self._base_args(total_duration=3.456))
        assert m["response_time"] == 3.46

    def test_time_to_first_token(self):
        m = _compute_final_metrics(**self._base_args(time_to_first_token=0.123))
        assert m["time_to_first_token"] == 0.12

    def test_time_to_first_token_none(self):
        m = _compute_final_metrics(**self._base_args(time_to_first_token=None))
        assert m["time_to_first_token"] == 0

    def test_model_returned(self):
        m = _compute_final_metrics(**self._base_args(model="gpt-4o"))
        assert m["model"] == "gpt-4o"

    def test_prep_timings_included(self):
        m = _compute_final_metrics(**self._base_args(
            time_to_first_token=1.25,
            prep_timings={"request_setup": 0.2, "tool_selection": 0.3, "prompt_build": 0.15},
        ))
        assert m["agent_prep_time"] == 0.65
        assert m["agent_model_wait_time"] == 0.6
        assert m["agent_prep_breakdown"] == {
            "request_setup": 0.2,
            "tool_selection": 0.3,
            "prompt_build": 0.15,
        }

    def test_tool_events_included(self):
        events = [{"tool": "bash", "duration": 1.0}]
        texts = ["round 1 text"]
        m = _compute_final_metrics(**self._base_args(
            tool_events=events,
            round_texts=texts,
        ))
        assert m["tool_events"] == events
        assert m["round_texts"] == texts

    def test_no_tool_events_excluded(self):
        m = _compute_final_metrics(**self._base_args(tool_events=[], round_texts=[]))
        assert "tool_events" not in m
        assert "round_texts" not in m


def test_agent_loop_mcp_disabled_map_and_prompt_helpers(monkeypatch):
    class Server:
        def __init__(self, id, disabled_tools):
            self.id = id
            self.disabled_tools = disabled_tools

    class DB:
        closed = False

        def query(self, _model):
            return self

        def all(self):
            return [
                Server("ok", '["read_file", "write_file"]'),
                Server("bad", "{not-json"),
                Server("empty", "[]"),
            ]

        def close(self):
            self.closed = True

    db = DB()
    core_db = types.ModuleType("core.database")
    core_db.McpServer = Server
    core_db.SessionLocal = lambda: db
    monkeypatch.setitem(sys.modules, "core.database", core_db)

    assert agent_loop._load_mcp_disabled_map() == {"ok": {"read_file", "write_file"}}
    assert db.closed is True

    monkeypatch.setattr(
        agent_loop,
        "TOOL_SECTIONS",
        {
            "bash": "```bash\nls\n```",
            "web_search": "- web search",
            "generate_image": "- image",
            "hidden": "- hidden",
            "extra1": "- extra1",
            "extra2": "- extra2",
            "extra3": "- extra3",
            "extra4": "- extra4",
            "extra5": "- extra5",
            "extra6": "- extra6",
        },
    )
    monkeypatch.setattr(agent_loop, "get_builtin_overrides", lambda: {"bash": "```bash\ncustom\n```"})

    compact = agent_loop._assemble_prompt({"bash", "web_search"}, compact=True)
    assert "Available tools: bash, web_search" in compact

    prompt = agent_loop._assemble_prompt({"bash", "web_search"}, disabled_tools={"hidden"})
    assert "custom" in prompt
    assert "## Additional tools" in prompt
    assert "web search" in prompt
    assert "more)" in prompt

    assert agent_loop._section_text("bash", "default").startswith("```bash")


def test_agent_loop_build_base_prompt_injects_skills_integrations_and_mcp(monkeypatch):
    monkeypatch.setattr(
        agent_loop,
        "TOOL_SECTIONS",
        {
            "bash": "```bash\nls\n```",
            "web_search": "- web search",
            "generate_image": "- image",
            "manage_session": "- sessions",
        },
    )
    monkeypatch.setattr(agent_loop, "get_builtin_overrides", lambda: {})
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: False if key == "image_gen_enabled" else default)

    tool_index = types.ModuleType("src.tool_index")
    tool_index.ALWAYS_AVAILABLE = {"bash"}
    monkeypatch.setitem(sys.modules, "src.tool_index", tool_index)

    class SkillsManager:
        def __init__(self, data_dir):
            self.data_dir = data_dir

        def index_for(self, owner=None, active_toolsets=None):
            assert owner == "alice"
            assert "generate_image" not in active_toolsets
            return [{"category": "workflow", "name": "local-test", "description": "Use local tests", "status": "draft"}]

    skills_mod = types.ModuleType("services.memory.skills")
    skills_mod.SkillsManager = SkillsManager
    monkeypatch.setitem(sys.modules, "services.memory.skills", skills_mod)

    constants_mod = types.ModuleType("src.constants")
    constants_mod.DATA_DIR = "data"
    monkeypatch.setitem(sys.modules, "src.constants", constants_mod)

    integrations_mod = types.ModuleType("src.integrations")
    integrations_mod.get_integrations_prompt = lambda: "INTEGRATIONS PROMPT"
    monkeypatch.setitem(sys.modules, "src.integrations", integrations_mod)

    class MCP:
        def get_tool_descriptions_for_prompt(self, disabled_map):
            assert disabled_map == {"srv": {"tool"}}
            return "\nMCP PROMPT"

    prompt = agent_loop._build_base_prompt(
        disabled_tools=set(),
        mcp_mgr=MCP(),
        needs_admin=True,
        relevant_tools={"web_search"},
        mcp_disabled_map={"srv": {"tool"}},
        owner="alice",
    )

    assert "web search" in prompt
    assert "local-test" in prompt
    assert "INTEGRATIONS PROMPT" in prompt
    assert "MCP PROMPT" in prompt
    assert "\n- image" not in prompt


def test_agent_loop_base_prompt_cache_is_scoped_by_owner(monkeypatch):
    monkeypatch.setattr(agent_loop, "_cached_base_prompt", None)
    monkeypatch.setattr(agent_loop, "_cached_base_prompt_key", None)
    monkeypatch.setattr(agent_loop, "get_builtin_overrides", lambda: {})
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)

    calls = []

    def fake_build(disabled_tools, mcp_mgr, needs_admin, relevant_tools=None, mcp_disabled_map=None, compact=False, owner=None):
        calls.append(owner)
        return f"prompt for {owner}"

    monkeypatch.setattr(agent_loop, "_build_base_prompt", fake_build)

    base_messages = [{"role": "user", "content": "hello"}]
    alice_messages, _ = agent_loop._build_system_prompt(
        list(base_messages),
        "model",
        None,
        None,
        disabled_tools=set(),
        owner="alice",
    )
    bob_messages, _ = agent_loop._build_system_prompt(
        list(base_messages),
        "model",
        None,
        None,
        disabled_tools=set(),
        owner="bob",
    )

    assert calls == ["alice", "bob"]
    assert alice_messages[0]["content"] == "prompt for alice"
    assert bob_messages[0]["content"] == "prompt for bob"


def test_agent_loop_message_context_tool_resolution_and_append(monkeypatch):
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "[Tool execution results]\n\nskip"},
        {"role": "user", "content": [{"type": "text", "text": "rename session"}]},
    ]

    assert agent_loop._extract_last_user_message(messages) == "rename session"
    assert agent_loop._recent_context_for_retrieval(messages, max_user=3) == "rename session\nfirst"

    native_block = SimpleNamespace(tool_type="bash")

    def fake_function_call_to_tool_block(name, args):
        return native_block if name == "ok_tool" else None

    monkeypatch.setattr(agent_loop, "function_call_to_tool_block", fake_function_call_to_tool_block)
    monkeypatch.setattr(agent_loop, "parse_tool_blocks", lambda text: [SimpleNamespace(tool_type="python")])

    blocks, used_native = agent_loop._resolve_tool_blocks(
        "```python\nprint(1)\n```",
        [
            {"id": "call-a", "name": "ok_tool", "arguments": "{}"},
            {"id": "call-b", "name": "bad_tool", "arguments": "{}"},
        ],
        2,
    )
    assert blocks == [native_block]
    assert used_native is True

    fenced_blocks, fenced_native = agent_loop._resolve_tool_blocks("```python\nprint(1)\n```", [], 3)
    assert fenced_blocks[0].tool_type == "python"
    assert fenced_native is False

    history = []
    agent_loop._append_tool_results(
        history,
        "",
        [{"id": "call-a", "name": "ok_tool", "arguments": "{}"}],
        ["formatted"],
        ["tool text"],
        True,
        1,
        round_reasoning="thinking",
    )
    assert history[0]["tool_calls"][0]["id"] == "call-a"
    assert history[0]["reasoning_content"] == "thinking"
    assert history[1] == {"role": "tool", "tool_call_id": "call-a", "content": "tool text"}

    agent_loop._append_tool_results(history, "round text", [], ["A", "B"], ["ignored"], False, 2)
    assert history[-2] == {"role": "assistant", "content": "round text"}
    assert "[Tool execution results]" in history[-1]["content"]

    snapshot = agent_loop._build_actions_snapshot(
        [
            {"tool": "bash", "command": "pytest", "output": "x" * 1300, "exit_code": 1},
            {"tool": "read_file", "output": ""},
        ],
        limit=300,
    )
    assert "[bash] pytest (exit 1)" in snapshot
    assert "..." not in snapshot
    assert len(snapshot) <= 300
