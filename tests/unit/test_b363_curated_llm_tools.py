"""B363 — curated LLM-wrapper tools: validate() smoke tests.

Each of the six tools (text_summarize, code_explain, email_draft,
commit_message, action_items_extract, tone_shift) builds on the
shared ``_prompt_template_base`` so the testing surface that needs
coverage is the per-tool validate() (input-schema enforcement) and
the per-tool _build_prompts() (correct system+user assembly).

execute() requires a live provider; that path is exercised by the
diagnostic harness section 07 (skill-smoke) when a daemon is up.
Per-tool unit tests here only need validate() + prompt construction.
"""
from __future__ import annotations

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.action_items_extract import (
    ActionItemsExtractTool,
)
from forest_soul_forge.tools.builtin.code_explain import CodeExplainTool
from forest_soul_forge.tools.builtin.commit_message import CommitMessageTool
from forest_soul_forge.tools.builtin.email_draft import EmailDraftTool
from forest_soul_forge.tools.builtin.text_summarize import TextSummarizeTool
from forest_soul_forge.tools.builtin.tone_shift import ToneShiftTool


def _ctx() -> ToolContext:
    """Minimal ToolContext for _build_prompts smoke tests. The
    prompt builders use ctx.role + ctx.genre + ctx.constraints; a
    bare ctx with sensible defaults is enough for validate()."""
    # ToolContext is a dataclass; constructing it requires a
    # specific field set. The fixture pattern across the suite is
    # tests/unit/conftest.py — but for prompt-construction smoke
    # we don't actually need the heavy fixtures. Just satisfy the
    # required fields with stubs.
    return ToolContext(
        instance_id="researcher_testabc123",
        agent_dna="test-agent-dna",
        role="researcher",
        session_id=None,
        genre="researcher",
        constraints={},
        provider=None,
    )


# ---- text_summarize -------------------------------------------------------

class TestTextSummarize:
    def test_validate_minimal(self):
        TextSummarizeTool().validate({"text": "Some content."})

    def test_validate_with_all_options(self):
        TextSummarizeTool().validate({
            "text": "x" * 100,
            "target_words": 50,
            "style": "bullet_points",
            "focus": "what happened",
            "max_tokens": 400,
        })

    def test_rejects_missing_text(self):
        with pytest.raises(ToolValidationError, match="text is required"):
            TextSummarizeTool().validate({})

    def test_rejects_empty_text(self):
        with pytest.raises(ToolValidationError, match="must not be empty"):
            TextSummarizeTool().validate({"text": ""})

    def test_rejects_invalid_style(self):
        with pytest.raises(ToolValidationError, match="style must be one of"):
            TextSummarizeTool().validate({"text": "x", "style": "haiku"})

    def test_rejects_out_of_range_target_words(self):
        with pytest.raises(ToolValidationError, match="target_words"):
            TextSummarizeTool().validate({"text": "x", "target_words": 9})

    def test_build_prompts_includes_text(self):
        sys_p, user_p = TextSummarizeTool()._build_prompts(
            {"text": "Alpha bravo charlie."}, _ctx(),
        )
        assert "Alpha bravo charlie." in user_p
        assert "summariz" in sys_p.lower()


# ---- code_explain ---------------------------------------------------------

class TestCodeExplain:
    def test_validate_minimal(self):
        CodeExplainTool().validate({"code": "def f(): pass"})

    def test_rejects_invalid_audience(self):
        with pytest.raises(ToolValidationError, match="audience must be one of"):
            CodeExplainTool().validate({"code": "x", "audience": "child"})

    def test_build_prompts_includes_code(self):
        sys_p, user_p = CodeExplainTool()._build_prompts(
            {"code": "def factorial(n): return 1 if n<2 else n*factorial(n-1)"},
            _ctx(),
        )
        assert "factorial" in user_p
        assert "explain" in sys_p.lower()


# ---- email_draft ----------------------------------------------------------

class TestEmailDraft:
    def test_validate_minimal(self):
        EmailDraftTool().validate({"intent": "Thank the team for shipping."})

    def test_validate_with_all_options(self):
        EmailDraftTool().validate({
            "intent": "Apologize for the delay.",
            "recipient": "Alex",
            "sender": "Bot",
            "tone": "apologetic",
            "length": "short",
        })

    def test_rejects_invalid_tone(self):
        with pytest.raises(ToolValidationError, match="tone must be one of"):
            EmailDraftTool().validate({"intent": "x", "tone": "sarcastic"})

    def test_build_prompts_includes_intent(self):
        sys_p, user_p = EmailDraftTool()._build_prompts(
            {"intent": "Confirm meeting at noon.", "tone": "friendly"},
            _ctx(),
        )
        assert "Confirm meeting at noon." in user_p
        assert "friendly" in sys_p.lower() or "friendly" in user_p.lower()


# ---- commit_message -------------------------------------------------------

class TestCommitMessage:
    def test_validate_minimal(self):
        CommitMessageTool().validate({"diff": "--- a\n+++ b\n+line"})

    def test_rejects_invalid_format(self):
        with pytest.raises(ToolValidationError, match="format must be one of"):
            CommitMessageTool().validate({"diff": "x", "format": "haiku"})

    def test_build_prompts_conventional_format(self):
        sys_p, user_p = CommitMessageTool()._build_prompts(
            {"diff": "diff content", "format": "conventional", "scope": "harness"},
            _ctx(),
        )
        assert "diff content" in user_p
        assert "Conventional" in sys_p


# ---- action_items_extract -------------------------------------------------

class TestActionItemsExtract:
    def test_validate_minimal(self):
        ActionItemsExtractTool().validate({"text": "Meeting log."})

    def test_validate_require_owner(self):
        ActionItemsExtractTool().validate({"text": "x", "require_owner": True})

    def test_rejects_non_bool_require_owner(self):
        with pytest.raises(ToolValidationError, match="require_owner must be a boolean"):
            ActionItemsExtractTool().validate({"text": "x", "require_owner": "yes"})

    def test_rejects_oversize_limit(self):
        with pytest.raises(ToolValidationError, match="limit must be int"):
            ActionItemsExtractTool().validate({"text": "x", "limit": 200})

    def test_build_prompts_includes_text(self):
        sys_p, user_p = ActionItemsExtractTool()._build_prompts(
            {"text": "Alex will ship B363 by Tuesday."}, _ctx(),
        )
        assert "Alex will ship B363 by Tuesday." in user_p
        assert "action item" in sys_p.lower()


# ---- tone_shift -----------------------------------------------------------

class TestToneShift:
    def test_validate_minimal(self):
        ToneShiftTool().validate({"text": "Hello.", "target_tone": "formal"})

    def test_rejects_missing_target_tone(self):
        with pytest.raises(ToolValidationError, match="target_tone must be one of"):
            ToneShiftTool().validate({"text": "x"})

    def test_rejects_invalid_tone(self):
        with pytest.raises(ToolValidationError, match="target_tone must be one of"):
            ToneShiftTool().validate({
                "text": "x", "target_tone": "shakespearean",
            })

    def test_build_prompts_includes_text_and_tone_hint(self):
        sys_p, user_p = ToneShiftTool()._build_prompts(
            {"text": "lol u r doing great", "target_tone": "executive_summary"},
            _ctx(),
        )
        assert "lol u r doing great" in user_p
        # The system prompt should reference the executive_summary
        # tone characteristics, not the literal string "executive_summary".
        assert any(
            kw in sys_p.lower() for kw in ("bottom line", "paragraph")
        )


# ---- catalog presence -----------------------------------------------------

def test_all_six_tools_in_catalog():
    """The diagnostic-all section 02 + 04 enforce this at run time,
    but a unit test catches catalog drift on commit-time."""
    from forest_soul_forge.core.tool_catalog import load_catalog
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    catalog = load_catalog(repo / "config" / "tool_catalog.yaml")
    expected = {
        "text_summarize.v1", "code_explain.v1", "email_draft.v1",
        "commit_message.v1", "action_items_extract.v1", "tone_shift.v1",
    }
    missing = expected - set(catalog.tools.keys())
    assert not missing, f"catalog missing: {missing}"


def test_all_six_tools_register():
    """register_builtins should put all six in the registry."""
    from forest_soul_forge.tools.base import ToolRegistry
    from forest_soul_forge.tools.builtin import register_builtins
    reg = ToolRegistry()
    register_builtins(reg)
    keys = set(reg.all_keys())
    expected = {
        "text_summarize.v1", "code_explain.v1", "email_draft.v1",
        "commit_message.v1", "action_items_extract.v1", "tone_shift.v1",
    }
    missing = expected - keys
    assert not missing, f"registry missing: {missing}"
