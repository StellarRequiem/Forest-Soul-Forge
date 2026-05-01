"""Unit tests for Tool Forge static analysis — ADR-0030 T2.

One test class per check, plus an integration test that runs the full
analyze() over a hand-crafted "good" tool source to confirm the pass
is silent on legitimate code.
"""
from __future__ import annotations

import textwrap

from forest_soul_forge.forge.static_analysis import (
    AnalysisResult,
    Flag,
    analyze,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal "shaped" tool source the other checks build on.
# ---------------------------------------------------------------------------
#
# History note: a prior version of this helper interpolated ``body``
# directly into a triple-quoted f-string before calling textwrap.dedent.
# That broke when dedent's common-indent calculation included the body's
# (already-indented) lines and stripped 4 spaces from everything,
# leaving body lines under-indented relative to the ``async def execute``
# they were meant to sit inside. Result: every dependent test failed
# with ``parse_error: expected an indented block``.
#
# Fixed by separating concerns: textwrap.dedent over the structural
# template only, then textwrap.indent the body to the method-body
# level (8 spaces deep — 4 for class, 4 for method) before splicing.
# Now ``body`` is supplied at zero indentation and the helper handles
# placement.
def _shaped_tool(*, side_effects: str = "read_only", body: str = "return None") -> str:
    """A class with the Tool Protocol shape — passes the shape check.
    Other tests append code to introduce specific flags.

    ``body`` is the body of the ``execute`` method, supplied with NO
    leading indentation. The helper indents it to the method-body level.
    """
    # Build the structural template with no body marker; use string
    # concatenation so dedent only touches the static template lines.
    header = textwrap.dedent(f'''\
    """Test tool."""
    from __future__ import annotations
    from typing import Any


    class T:
        name = "t"
        version = "1"
        side_effects = "{side_effects}"

        def validate(self, args: dict[str, Any]) -> None:
            return None

        async def execute(self, args, ctx):
    ''')
    # Body sits 8 spaces deep — once for class, once for method.
    body_indented = textwrap.indent(
        textwrap.dedent(body).strip("\n") or "pass",
        " " * 8,
    )
    return header + body_indented + "\n"


def _flag_rules(result: AnalysisResult) -> list[str]:
    return [f.rule for f in result.flags]


# ---------------------------------------------------------------------------
# Hard checks
# ---------------------------------------------------------------------------
class TestParseError:
    def test_unparseable_source_hard_flagged(self):
        result = analyze("def foo(:\n    pass", declared_side_effects="read_only")
        assert result.parsed_tree is None
        assert result.install_blocked
        assert "parse_error" in _flag_rules(result)


class TestToolClassShape:
    def test_no_class_at_all(self):
        result = analyze(
            "x = 1\nimport os\n",
            declared_side_effects="read_only",
        )
        assert "no_tool_class" in _flag_rules(result)
        assert result.install_blocked

    def test_class_missing_required_attr(self):
        # Has a class with name + version but no side_effects.
        src = textwrap.dedent('''
        class Half:
            name = "half"
            version = "1"
            def validate(self, args): return None
            async def execute(self, args, ctx): return None
        ''').strip()
        result = analyze(src, declared_side_effects="read_only")
        assert "no_tool_class" in _flag_rules(result)

    def test_shaped_class_passes_shape_check(self):
        result = analyze(_shaped_tool(), declared_side_effects="read_only")
        assert "no_tool_class" not in _flag_rules(result)

    def test_sync_execute_hard_flagged(self):
        src = textwrap.dedent('''
        class T:
            name = "t"
            version = "1"
            side_effects = "read_only"
            def validate(self, args): return None
            def execute(self, args, ctx): return None
        ''').strip()
        result = analyze(src, declared_side_effects="read_only")
        assert "execute_must_be_async" in _flag_rules(result)
        assert result.install_blocked

    def test_multiple_tool_classes_soft(self):
        src = textwrap.dedent('''
        from typing import Any
        class A:
            name = "a"
            version = "1"
            side_effects = "read_only"
            def validate(self, args): return None
            async def execute(self, args, ctx): return None
        class B:
            name = "b"
            version = "1"
            side_effects = "read_only"
            def validate(self, args): return None
            async def execute(self, args, ctx): return None
        ''').strip()
        result = analyze(src, declared_side_effects="read_only")
        assert "multiple_tool_classes" in _flag_rules(result)
        # Soft only — not blocked.
        assert not result.install_blocked


class TestInvalidSideEffects:
    def test_unknown_value_hard_flagged(self):
        src = _shaped_tool(side_effects="telekinesis")
        result = analyze(src, declared_side_effects="telekinesis")
        assert "invalid_side_effects" in _flag_rules(result)
        assert result.install_blocked


class TestForbiddenCalls:
    def test_eval_hard_flagged(self):
        body = 'eval("1+1")\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "forbidden_builtin" in _flag_rules(result)
        assert result.install_blocked

    def test_exec_hard_flagged(self):
        body = 'exec("x=1")\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "forbidden_builtin" in _flag_rules(result)

    def test_os_system_hard_flagged(self):
        body = 'import os\nos.system("ls")\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="external")
        assert "os_system" in _flag_rules(result)


class TestForbiddenAttributeAccess:
    def test_dunder_import_hard_flagged(self):
        body = '__import__("os")\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "dynamic_import" in _flag_rules(result)
        assert result.install_blocked

    def test_builtins_access_hard_flagged(self):
        body = 'x = __builtins__\nreturn x'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "builtins_access" in _flag_rules(result)


# ---------------------------------------------------------------------------
# Soft checks
# ---------------------------------------------------------------------------
class TestNetworkImports:
    def test_requests_in_read_only_soft_flagged(self):
        src = "import requests\n" + _shaped_tool(side_effects="read_only")
        result = analyze(src, declared_side_effects="read_only")
        assert "network_import_in_read_only" in _flag_rules(result)
        assert not result.install_blocked  # soft only

    def test_from_urllib_in_read_only_soft_flagged(self):
        src = "from urllib.request import urlopen\n" + _shaped_tool()
        result = analyze(src, declared_side_effects="read_only")
        assert "network_import_in_read_only" in _flag_rules(result)

    def test_network_in_network_tier_no_flag(self):
        src = "import requests\n" + _shaped_tool(side_effects="network")
        result = analyze(src, declared_side_effects="network")
        # Spec correctly reflects the import — no flag.
        assert "network_import_in_read_only" not in _flag_rules(result)


class TestFilesystemWrites:
    def test_open_write_in_read_only_soft_flagged(self):
        body = 'with open("/tmp/x", "w") as f:\n    f.write("x")\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "filesystem_write_in_low_tier" in _flag_rules(result)
        assert not result.install_blocked

    def test_path_write_text_in_network_soft_flagged(self):
        body = 'from pathlib import Path\nPath("/tmp/x").write_text("y")\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="network")
        assert "filesystem_write_in_low_tier" in _flag_rules(result)

    def test_open_write_in_filesystem_tier_no_flag(self):
        body = 'with open("/tmp/x", "w") as f:\n    f.write("x")\nreturn None'
        src = _shaped_tool(body=body, side_effects="filesystem")
        result = analyze(src, declared_side_effects="filesystem")
        assert "filesystem_write_in_low_tier" not in _flag_rules(result)


class TestSubprocessUse:
    def test_import_subprocess_soft_flagged(self):
        src = "import subprocess\n" + _shaped_tool(side_effects="filesystem")
        result = analyze(src, declared_side_effects="filesystem")
        assert "subprocess_used" in _flag_rules(result)
        assert not result.install_blocked

    def test_from_subprocess_soft_flagged(self):
        src = "from subprocess import run\n" + _shaped_tool(side_effects="filesystem")
        result = analyze(src, declared_side_effects="filesystem")
        assert "subprocess_used" in _flag_rules(result)


class TestMissingTokensPlumbing:
    def test_provider_call_without_tokens_in_result_soft_flagged(self):
        body = textwrap.dedent('''
                reply = await ctx.provider.complete("hi")
                return ToolResult(output={"reply": reply})
        ''').rstrip()
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="network")
        assert "missing_tokens_plumbing" in _flag_rules(result)

    def test_provider_call_with_tokens_no_flag(self):
        body = textwrap.dedent('''
                reply = await ctx.provider.complete("hi")
                return ToolResult(output={"reply": reply}, tokens_used=42)
        ''').rstrip()
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="network")
        assert "missing_tokens_plumbing" not in _flag_rules(result)

    def test_no_provider_call_no_flag(self):
        body = 'return None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "missing_tokens_plumbing" not in _flag_rules(result)


class TestTodoMarkers:
    def test_todo_soft_flagged(self):
        body = '# TODO: actually compute the result\nreturn None'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "hedge_marker" in _flag_rules(result)

    def test_fixme_soft_flagged(self):
        body = 'return None  # FIXME: edge case'
        src = _shaped_tool(body=body)
        result = analyze(src, declared_side_effects="read_only")
        assert "hedge_marker" in _flag_rules(result)


# ---------------------------------------------------------------------------
# Clean source
# ---------------------------------------------------------------------------
class TestCleanSource:
    def test_well_formed_pure_function_zero_flags(self):
        """A correctly-shaped, side-effects-honest, no-LLM, no-marker
        tool should produce zero flags. Any false positive here is a
        bug in the analyzer."""
        src = textwrap.dedent('''
        """A pure-function reference tool."""
        from __future__ import annotations
        from typing import Any


        class T:
            name = "t"
            version = "1"
            side_effects = "read_only"

            def validate(self, args: dict[str, Any]) -> None:
                if "x" not in args:
                    raise ValueError("missing x")

            async def execute(self, args, ctx):
                return {"y": args["x"] * 2}
        ''').strip()
        result = analyze(src, declared_side_effects="read_only")
        assert result.flags == ()
        assert not result.install_blocked
