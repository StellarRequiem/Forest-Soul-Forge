"""Phase B closing smoke tests for the 8 tools that had zero unit coverage.

Phase A audit 2026-04-30 finding T-1: 8 of 40 registered tools shipped
without dedicated unit tests. The youngest tools (SW-track + open-web)
correlated with recency. This file covers the validate() surface for
every one of the eight + the reachable-without-heavy-mocking execute
paths.

Tools covered:
  - code_read.v1     (SW-track, read_only)
  - code_edit.v1     (SW-track, filesystem)
  - shell_exec.v1    (SW-track, external)
  - llm_think.v1     (bridge tool, read_only)
  - mcp_call.v1      (open-web, external)
  - browser_action.v1 (open-web, external) — validate only; playwright
                       execute path needs heavy mocks, defer to v0.3
  - suggest_agent.v1 (G6, read_only)
  - memory_verify.v1 (K1, filesystem)

Strategy: small batched tests per tool. Each tool gets the validate()
matrix + the simplest reachable execute() path. No exhaustive coverage
here — that's what targeted-per-tool test files would do later.
"""
from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.browser_action import BrowserActionTool
from forest_soul_forge.tools.builtin.code_edit import CodeEditError, CodeEditTool
from forest_soul_forge.tools.builtin.code_read import CodeReadError, CodeReadTool
from forest_soul_forge.tools.builtin.llm_think import LlmThinkTool
from forest_soul_forge.tools.builtin.mcp_call import McpCallTool
from forest_soul_forge.tools.builtin.memory_verify import MemoryVerifyTool
from forest_soul_forge.tools.builtin.shell_exec import ShellExecTool
from forest_soul_forge.tools.builtin.suggest_agent import SuggestAgentTool


def _run(coro):
    return asyncio.run(coro)


def _ctx(**overrides) -> ToolContext:
    base = dict(
        instance_id="i1", agent_dna="d" * 12,
        role="software_engineer", genre="actuator",
        session_id="s1", constraints={},
    )
    base.update(overrides)
    return ToolContext(**base)


# ===========================================================================
# code_read.v1
# ===========================================================================
class TestCodeReadValidate:
    def test_path_required(self):
        with pytest.raises(ToolValidationError, match="path"):
            CodeReadTool().validate({})

    def test_path_must_be_string(self):
        with pytest.raises(ToolValidationError, match="path"):
            CodeReadTool().validate({"path": 42})

    def test_max_bytes_out_of_range(self):
        with pytest.raises(ToolValidationError, match="max_bytes"):
            CodeReadTool().validate({"path": "x", "max_bytes": -1})

    def test_valid_args_pass(self):
        CodeReadTool().validate({"path": "/some/file.py"})


class TestCodeReadExecute:
    def test_no_allowed_paths_refuses(self):
        with pytest.raises(CodeReadError, match="allowed_paths"):
            _run(CodeReadTool().execute({"path": "/etc/passwd"}, _ctx()))

    def test_path_outside_allowlist_refuses(self, tmp_path):
        # tmp_path is allowed, but we ask for /etc/passwd
        ctx = _ctx(constraints={"allowed_paths": [str(tmp_path)]})
        with pytest.raises(CodeReadError, match="outside"):
            _run(CodeReadTool().execute({"path": "/etc/passwd"}, ctx))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = _ctx(constraints={"allowed_paths": [str(tmp_path)]})
        with pytest.raises(CodeReadError, match="does not exist"):
            _run(CodeReadTool().execute(
                {"path": str(tmp_path / "nope.py")}, ctx,
            ))

    def test_directory_refuses(self, tmp_path):
        ctx = _ctx(constraints={"allowed_paths": [str(tmp_path)]})
        with pytest.raises(CodeReadError, match="not a regular file"):
            _run(CodeReadTool().execute({"path": str(tmp_path)}, ctx))

    def test_happy_path_round_trips(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("print('hello')\n")
        ctx = _ctx(constraints={"allowed_paths": [str(tmp_path)]})
        out = _run(CodeReadTool().execute({"path": str(f)}, ctx))
        assert out.output["content"] == "print('hello')\n"
        assert out.output["truncated"] is False
        assert out.output["sha256"]
        assert out.output["bytes_read"] == out.output["size_bytes"]


# ===========================================================================
# code_edit.v1
# ===========================================================================
class TestCodeEditValidate:
    def test_path_required(self):
        with pytest.raises(ToolValidationError, match="path"):
            CodeEditTool().validate({"content": "x"})

    def test_content_required(self):
        with pytest.raises(ToolValidationError, match="content"):
            CodeEditTool().validate({"path": "x"})

    def test_unknown_mode_rejected(self):
        with pytest.raises(ToolValidationError, match="mode"):
            CodeEditTool().validate({"path": "x", "content": "y", "mode": "garbage"})

    def test_valid_args_pass(self):
        CodeEditTool().validate({"path": "/x", "content": "y"})


class TestCodeEditExecute:
    def test_no_allowed_paths_refuses(self):
        with pytest.raises(CodeEditError, match="allowed_paths"):
            _run(CodeEditTool().execute(
                {"path": "/tmp/x", "content": "x"}, _ctx(),
            ))

    def test_path_outside_allowlist_refuses(self, tmp_path):
        ctx = _ctx(constraints={"allowed_paths": [str(tmp_path)]})
        with pytest.raises(CodeEditError, match="outside"):
            _run(CodeEditTool().execute(
                {"path": "/etc/test.txt", "content": "x"}, ctx,
            ))

    def test_happy_path_writes_file(self, tmp_path):
        ctx = _ctx(constraints={"allowed_paths": [str(tmp_path)]})
        target = tmp_path / "new_file.py"
        out = _run(CodeEditTool().execute(
            {"path": str(target), "content": "fresh content\n"}, ctx,
        ))
        assert target.read_text() == "fresh content\n"
        assert out.output["bytes_written"] == len("fresh content\n")
        assert out.output["created"] is True
        assert out.output["mode"] == "write"


# ===========================================================================
# shell_exec.v1 (validate only — execute requires allowed_commands path setup)
# ===========================================================================
class TestShellExecValidate:
    def test_argv_required(self):
        with pytest.raises(ToolValidationError, match="argv"):
            ShellExecTool().validate({})

    def test_argv_must_be_list(self):
        with pytest.raises(ToolValidationError, match="argv"):
            ShellExecTool().validate({"argv": "ls -la"})

    def test_argv_empty_rejected(self):
        with pytest.raises(ToolValidationError, match="argv"):
            ShellExecTool().validate({"argv": []})

    def test_argv_non_string_element_rejected(self):
        with pytest.raises(ToolValidationError, match="argv\\["):
            ShellExecTool().validate({"argv": ["ls", 42]})

    def test_argv0_with_path_separator_rejected(self):
        """Path lookup is the gate — bare command names only."""
        with pytest.raises(ToolValidationError, match="path separator"):
            ShellExecTool().validate({"argv": ["/bin/ls"]})

    def test_argv0_starting_with_dash_rejected(self):
        """First element must be a command name, not a flag."""
        with pytest.raises(ToolValidationError, match="must be"):
            ShellExecTool().validate({"argv": ["-l"]})

    def test_timeout_out_of_range(self):
        with pytest.raises(ToolValidationError, match="timeout_s"):
            ShellExecTool().validate({"argv": ["ls"], "timeout_s": -1})

    def test_valid_args_pass(self):
        ShellExecTool().validate({"argv": ["ls", "-la"]})


# ===========================================================================
# llm_think.v1
# ===========================================================================
class TestLlmThinkValidate:
    def test_prompt_required(self):
        with pytest.raises(ToolValidationError, match="prompt"):
            LlmThinkTool().validate({})

    def test_empty_prompt_rejected(self):
        with pytest.raises(ToolValidationError, match="prompt"):
            LlmThinkTool().validate({"prompt": ""})

    def test_huge_prompt_rejected(self):
        with pytest.raises(ToolValidationError, match="too long"):
            LlmThinkTool().validate({"prompt": "x" * 33_000})

    def test_unknown_task_kind_rejected(self):
        with pytest.raises(ToolValidationError, match="task_kind"):
            LlmThinkTool().validate({"prompt": "x", "task_kind": "garbage"})

    def test_max_tokens_out_of_range(self):
        with pytest.raises(ToolValidationError, match="max_tokens"):
            LlmThinkTool().validate({"prompt": "x", "max_tokens": 0})
        with pytest.raises(ToolValidationError, match="max_tokens"):
            LlmThinkTool().validate({"prompt": "x", "max_tokens": 99_999})

    def test_invalid_temperature_rejected(self):
        with pytest.raises(ToolValidationError, match="temperature"):
            LlmThinkTool().validate({"prompt": "x", "temperature": 5})

    def test_valid_args_pass(self):
        LlmThinkTool().validate({"prompt": "hello"})


class TestLlmThinkExecute:
    def test_no_provider_refuses(self):
        with pytest.raises(ToolValidationError, match="no LLM provider"):
            _run(LlmThinkTool().execute({"prompt": "hi"}, _ctx(provider=None)
                                         if hasattr(ToolContext, "provider")
                                         else _ctx()))

    def test_provider_exception_translated(self):
        from forest_soul_forge.daemon.providers import TaskKind

        class _Stub:
            name = "stub"
            models = {TaskKind.CONVERSATION: "stub:1"}

            async def complete(self, *a, **k):
                raise RuntimeError("provider exploded")

        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="x", genre=None, session_id="s",
            constraints={}, provider=_Stub(),
        )
        with pytest.raises(ToolValidationError, match="provider.complete failed"):
            _run(LlmThinkTool().execute({"prompt": "hi"}, ctx))


# ===========================================================================
# mcp_call.v1 (validate only)
# ===========================================================================
class TestMcpCallValidate:
    def test_server_name_required(self):
        with pytest.raises(ToolValidationError, match="server_name"):
            McpCallTool().validate({"tool_name": "x"})

    def test_tool_name_required(self):
        with pytest.raises(ToolValidationError, match="tool_name"):
            McpCallTool().validate({"server_name": "x"})

    def test_args_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="args must be"):
            McpCallTool().validate({
                "server_name": "x", "tool_name": "y", "args": "string-not-dict",
            })

    def test_auth_secret_name_must_be_string(self):
        with pytest.raises(ToolValidationError, match="auth_secret_name"):
            McpCallTool().validate({
                "server_name": "x", "tool_name": "y", "auth_secret_name": 42,
            })

    def test_negative_timeout_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout_s"):
            McpCallTool().validate({
                "server_name": "x", "tool_name": "y", "timeout_s": -1,
            })

    def test_valid_args_pass(self):
        McpCallTool().validate({"server_name": "x", "tool_name": "y"})


# ===========================================================================
# browser_action.v1 (validate only — playwright execute deferred)
# ===========================================================================
class TestBrowserActionValidate:
    def test_url_required(self):
        with pytest.raises(ToolValidationError, match="url"):
            BrowserActionTool().validate({})

    def test_non_http_scheme_rejected(self):
        with pytest.raises(ToolValidationError, match="scheme"):
            BrowserActionTool().validate({"url": "file:///etc/passwd"})

    def test_no_host_rejected(self):
        with pytest.raises(ToolValidationError, match="host"):
            BrowserActionTool().validate({"url": "http://"})

    def test_actions_must_be_list(self):
        with pytest.raises(ToolValidationError, match="actions must be"):
            BrowserActionTool().validate({
                "url": "https://x.com", "actions": "not-a-list",
            })

    def test_action_unknown_type_rejected(self):
        with pytest.raises(ToolValidationError, match="actions\\[0\\].type"):
            BrowserActionTool().validate({
                "url": "https://x.com", "actions": [{"type": "garbage"}],
            })

    def test_action_click_missing_selector(self):
        with pytest.raises(ToolValidationError, match="click.*selector"):
            BrowserActionTool().validate({
                "url": "https://x.com", "actions": [{"type": "click"}],
            })

    def test_action_type_missing_selector(self):
        with pytest.raises(ToolValidationError, match="type.*selector"):
            BrowserActionTool().validate({
                "url": "https://x.com",
                "actions": [{"type": "type", "text": "hi"}],
            })

    def test_action_type_missing_text(self):
        with pytest.raises(ToolValidationError, match="text"):
            BrowserActionTool().validate({
                "url": "https://x.com",
                "actions": [{"type": "type", "selector": "#input"}],
            })

    def test_valid_args_pass(self):
        BrowserActionTool().validate({
            "url": "https://example.com",
            "actions": [
                {"type": "click", "selector": "#button"},
                {"type": "type", "selector": "#input", "text": "hi"},
            ],
        })


# ===========================================================================
# suggest_agent.v1 (validate only — execute needs agent_registry shape)
# ===========================================================================
class TestSuggestAgentValidate:
    def test_task_required(self):
        with pytest.raises(ToolValidationError, match="task"):
            SuggestAgentTool().validate({})

    def test_empty_task_rejected(self):
        with pytest.raises(ToolValidationError, match="task"):
            SuggestAgentTool().validate({"task": "   "})

    def test_top_k_out_of_range(self):
        with pytest.raises(ToolValidationError, match="top_k"):
            SuggestAgentTool().validate({"task": "x", "top_k": 0})
        with pytest.raises(ToolValidationError, match="top_k"):
            SuggestAgentTool().validate({"task": "x", "top_k": 1000})

    def test_filter_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="filter must be"):
            SuggestAgentTool().validate({"task": "x", "filter": "not-a-dict"})

    def test_filter_genre_must_be_string(self):
        with pytest.raises(ToolValidationError, match="filter.genre"):
            SuggestAgentTool().validate({"task": "x", "filter": {"genre": 42}})

    def test_valid_args_pass(self):
        SuggestAgentTool().validate({"task": "summarize this"})
        SuggestAgentTool().validate({
            "task": "x", "top_k": 5, "filter": {"genre": "researcher"},
        })


# ===========================================================================
# memory_verify.v1 (validate only — execute needs memory dependency)
# ===========================================================================
class TestMemoryVerifyValidate:
    def test_entry_id_required(self):
        with pytest.raises(ToolValidationError, match="entry_id"):
            MemoryVerifyTool().validate({"verifier_id": "alex"})

    def test_verifier_id_required(self):
        with pytest.raises(ToolValidationError, match="verifier_id"):
            MemoryVerifyTool().validate({"entry_id": "e1"})

    def test_seal_note_must_be_string(self):
        with pytest.raises(ToolValidationError, match="seal_note"):
            MemoryVerifyTool().validate({
                "entry_id": "e1", "verifier_id": "alex", "seal_note": 42,
            })

    def test_seal_note_too_long(self):
        with pytest.raises(ToolValidationError, match="too long"):
            MemoryVerifyTool().validate({
                "entry_id": "e1", "verifier_id": "alex",
                "seal_note": "x" * 600,
            })

    def test_valid_args_pass(self):
        MemoryVerifyTool().validate({"entry_id": "e1", "verifier_id": "alex"})


# ===========================================================================
# Registration smoke — every tool registers + side_effects matches
# ===========================================================================
class TestRegistration:
    def test_all_eight_tools_registered_at_lifespan(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        for name in (
            "code_read", "code_edit", "shell_exec", "llm_think",
            "mcp_call", "browser_action", "suggest_agent", "memory_verify",
        ):
            assert reg.has(name, "1"), f"{name}.v1 not registered"

    def test_side_effects_match_expected_classes(self):
        """Side-effects classification is load-bearing for genre-floor
        gating. Pin the contract."""
        assert CodeReadTool.side_effects == "read_only"
        assert CodeEditTool.side_effects == "filesystem"
        assert ShellExecTool.side_effects == "external"
        assert LlmThinkTool.side_effects == "read_only"
        assert McpCallTool.side_effects == "external"
        assert BrowserActionTool.side_effects == "external"
        assert SuggestAgentTool.side_effects == "read_only"
        assert MemoryVerifyTool.side_effects == "filesystem"
