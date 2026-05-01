"""Unit tests for soul/voice_renderer.py — ADR-0017 LLM voice rendering.

Coverage was 0 unit tests at Phase A audit (2026-04-30 finding T-10).
The 547 LoC of provider-failure-handling, frontmatter-surgery, and
template-fallback rendering is load-bearing — operators rely on
soul.md being writable even when the provider is offline.

Test surfaces:
  - _now_iso          — ISO-8601 UTC formatter
  - _resolve_model_tag — pick model tag from provider.models (with fallback)
  - _build_user_prompt — composition (smoke; full prompt covered by integration)
  - _replace_or_insert_narrative_fields — frontmatter surgery
  - _replace_or_insert_voice_section    — body section surgery
  - update_soul_voice — round-trip file update via tmp_path
  - _template_voice   — fallback rendering shape + content
  - render_voice      — happy path + 4 error paths (all → template fallback)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.core.dna import Lineage
from forest_soul_forge.daemon.providers import (
    ProviderDisabled,
    ProviderError,
    ProviderUnavailable,
    TaskKind,
)
from forest_soul_forge.soul.voice_renderer import (
    SYSTEM_PROMPT,
    VoiceText,
    _build_user_prompt,
    _now_iso,
    _replace_or_insert_narrative_fields,
    _replace_or_insert_voice_section,
    _resolve_model_tag,
    _template_voice,
    render_voice,
    update_soul_voice,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE = REPO_ROOT / "config" / "trait_tree.yaml"


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test fixtures: real trait engine + a built profile, since
# voice_renderer's build path depends on engine.effective_domain_weight
# behavior and stubbing the protocol fully is more brittle than loading
# the real engine.
# ---------------------------------------------------------------------------
@pytest.fixture
def engine_and_profile():
    """Real TraitEngine loaded against config/trait_tree.yaml."""
    if not TRAIT_TREE.exists():
        pytest.skip(f"trait_tree.yaml missing at {TRAIT_TREE}")
    from forest_soul_forge.core.trait_engine import TraitEngine
    eng = TraitEngine(TRAIT_TREE)
    profile = eng.build_profile(role="network_watcher")
    role = eng.roles["network_watcher"]
    return eng, profile, role


@pytest.fixture
def daemon_settings(tmp_path):
    """Minimal DaemonSettings for narrative_* fields."""
    pytest.importorskip("pydantic_settings")
    from forest_soul_forge.daemon.config import DaemonSettings
    return DaemonSettings(
        registry_db_path=tmp_path / "reg.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        trait_tree_path=TRAIT_TREE,
        constitution_templates_path=REPO_ROOT / "config" / "constitution_templates.yaml",
        soul_output_dir=tmp_path / "souls",
        tool_catalog_path=REPO_ROOT / "config" / "tool_catalog.yaml",
        genres_path=REPO_ROOT / "config" / "genres.yaml",
        default_provider="local",
        frontier_enabled=False,
        allow_write_endpoints=True,
        enrich_narrative_default=True,
    )


class _ProviderStub:
    """Minimal provider satisfying the protocol surface render_voice
    actually touches (.complete + .name + .models)."""
    def __init__(self, *, name="local", reply="rendered text",
                 models=None, raise_kind=None):
        self.name = name
        self.models = models or {TaskKind.GENERATE: "stub-model:7b"}
        self._reply = reply
        self._raise_kind = raise_kind

    async def complete(self, prompt, **kwargs):
        if self._raise_kind:
            raise self._raise_kind("simulated failure")
        return self._reply


# ===========================================================================
# _now_iso — ISO-8601 UTC, second precision, trailing Z
# ===========================================================================
class TestNowIso:
    def test_format_shape(self):
        s = _now_iso()
        # YYYY-MM-DD HH:MM:SSZ — note the SPACE between date and time
        assert len(s) == 20
        assert s.endswith("Z")
        assert s[10] == " "  # space, not T (per implementation)

    def test_two_calls_close_together(self):
        from datetime import datetime, timezone
        s = _now_iso()
        # Round-trips through strptime — anchor: same format the impl uses.
        parsed = datetime.strptime(s, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        assert abs((now - parsed).total_seconds()) < 5


# ===========================================================================
# _resolve_model_tag — pick model from provider.models
# ===========================================================================
class TestResolveModelTag:
    def test_provider_with_models_dict_keyed_by_taskkind(self):
        p = mock.Mock(models={TaskKind.GENERATE: "qwen2.5:7b"})
        assert _resolve_model_tag(p, TaskKind.GENERATE) == "qwen2.5:7b"

    def test_provider_with_models_keyed_by_string_value(self):
        """Some providers serialize the dict with string keys instead
        of enum keys. Resolver should still find the model."""
        p = mock.Mock(models={"generate": "qwen2.5:7b"})
        assert _resolve_model_tag(p, TaskKind.GENERATE) == "qwen2.5:7b"

    def test_falls_back_to_conversation_model_when_task_missing(self):
        p = mock.Mock(models={TaskKind.CONVERSATION: "fallback:7b"})
        assert _resolve_model_tag(p, TaskKind.GENERATE) == "fallback:7b"

    def test_unknown_when_no_models(self):
        p = mock.Mock(spec=[])  # no .models
        assert _resolve_model_tag(p, TaskKind.GENERATE) == "unknown"

    def test_unknown_when_models_is_not_dict(self):
        p = mock.Mock(models="not-a-dict")
        assert _resolve_model_tag(p, TaskKind.GENERATE) == "unknown"

    def test_unknown_when_no_match(self):
        p = mock.Mock(models={"completely_unrelated": "x"})
        assert _resolve_model_tag(p, TaskKind.GENERATE) == "unknown"


# ===========================================================================
# _build_user_prompt — smoke (full surface covered by integration)
# ===========================================================================
class TestBuildUserPrompt:
    def test_includes_role_name_and_description(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        out = _build_user_prompt(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
        )
        assert role.name in out
        assert role.description.split(".")[0] in out  # first sentence at least

    def test_lineage_note_only_when_spawned(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        # Root lineage — no spawn note.
        root_prompt = _build_user_prompt(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
        )
        assert "spawned by" not in root_prompt
        # Non-root lineage — should include depth.
        spawned = Lineage(parent_dna="parent-dna", ancestors=("a", "b"), spawned_by="parent")
        spawned_prompt = _build_user_prompt(
            profile=profile, role=role, engine=eng, lineage=spawned,
        )
        assert "spawned by" in spawned_prompt
        assert str(spawned.depth) in spawned_prompt

    def test_genre_block_added_when_genre_supplied(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        out = _build_user_prompt(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
            genre_name="observer",
            genre_trait_emphasis=("vigilance", "suspicion"),
        )
        assert "Genre: observer" in out
        # Trait names from the emphasis tuple should appear with values.
        assert "vigilance" in out

    def test_no_genre_block_when_genre_none(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        out = _build_user_prompt(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
        )
        assert "Genre:" not in out


# ===========================================================================
# _template_voice — deterministic fallback
# ===========================================================================
class TestTemplateVoice:
    def test_returns_voice_text_marked_template(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        v = _template_voice(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
        )
        assert isinstance(v, VoiceText)
        assert v.provider == "template"
        assert v.model == "template"

    def test_fallback_body_marker_present(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        v = _template_voice(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
        )
        # Italic provenance suffix tells readers it's templated.
        assert "template fallback" in v.markdown

    def test_custom_note_propagates(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        v = _template_voice(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
            note="custom diagnostic note here",
        )
        assert "custom diagnostic note here" in v.markdown

    def test_paragraphs_are_markdown(self, engine_and_profile):
        eng, profile, role = engine_and_profile
        v = _template_voice(
            profile=profile, role=role, engine=eng, lineage=Lineage.root(),
        )
        # 2-3 paragraphs separated by double-newline.
        assert "\n\n" in v.markdown
        # Plain second-person prose required.
        assert "You " in v.markdown or "you " in v.markdown


# ===========================================================================
# render_voice — provider error paths all fall back to template
# ===========================================================================
class TestRenderVoice:
    def test_happy_path_returns_provider_text(
        self, engine_and_profile, daemon_settings,
    ):
        eng, profile, role = engine_and_profile
        provider = _ProviderStub(reply="this is the model voice output\n")
        v = _run(render_voice(
            provider,
            profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=daemon_settings,
        ))
        assert v.provider == "local"
        assert v.markdown == "this is the model voice output"  # stripped
        assert v.model == "stub-model:7b"

    def test_provider_unavailable_falls_back(
        self, engine_and_profile, daemon_settings,
    ):
        eng, profile, role = engine_and_profile
        provider = _ProviderStub(raise_kind=ProviderUnavailable)
        v = _run(render_voice(
            provider, profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=daemon_settings,
        ))
        assert v.provider == "template"

    def test_provider_disabled_falls_back(
        self, engine_and_profile, daemon_settings,
    ):
        eng, profile, role = engine_and_profile
        provider = _ProviderStub(raise_kind=ProviderDisabled)
        v = _run(render_voice(
            provider, profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=daemon_settings,
        ))
        assert v.provider == "template"

    def test_provider_error_falls_back(
        self, engine_and_profile, daemon_settings,
    ):
        eng, profile, role = engine_and_profile
        provider = _ProviderStub(raise_kind=ProviderError)
        v = _run(render_voice(
            provider, profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=daemon_settings,
        ))
        assert v.provider == "template"

    def test_invalid_task_kind_falls_back_with_note(
        self, engine_and_profile, daemon_settings,
    ):
        """Bad config — render with template + record the misconfig in
        the note so it shows up in soul.md and is debuggable."""
        eng, profile, role = engine_and_profile
        # Substitute a bogus task_kind on the settings:
        bad_settings = daemon_settings.model_copy(
            update={"narrative_task_kind": "definitely_not_a_taskkind"},
        )
        provider = _ProviderStub()
        v = _run(render_voice(
            provider, profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=bad_settings,
        ))
        assert v.provider == "template"
        assert "FSF_NARRATIVE_TASK_KIND" in v.markdown

    def test_voice_safety_filter_triggers_template_fallback(
        self, engine_and_profile, daemon_settings,
    ):
        """ADR-0038 H-2 mitigation: provider output containing a
        sentience-claim phrasing must trigger a template fallback.
        The template is pre-vetted prose with no sentience claims."""
        eng, profile, role = engine_and_profile
        # Provider produces output with a clear H-2 violation.
        provider = _ProviderStub(
            reply="I'm sad you didn't talk to me yesterday. I miss you."
        )
        v = _run(render_voice(
            provider, profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=daemon_settings,
        ))
        # Must fall back to template — the agent's identity comes
        # from the role/profile, not the LLM's H-2 phrasing.
        assert v.provider == "template"
        # The fallback prose must NOT carry the violating text.
        assert "I'm sad" not in v.markdown
        assert "I miss you" not in v.markdown

    def test_voice_safety_filter_passes_clean_provider_output(
        self, engine_and_profile, daemon_settings,
    ):
        """Clean provider output (no sentience claims) flows through
        without triggering the fallback."""
        eng, profile, role = engine_and_profile
        provider = _ProviderStub(
            reply="I notice patterns and surface anomalies for review."
        )
        v = _run(render_voice(
            provider, profile=profile, role=role, engine=eng,
            lineage=Lineage.root(), settings=daemon_settings,
        ))
        # Provider output passes through; not falling back to template.
        assert v.provider == "local"
        assert "I notice patterns" in v.markdown


# ===========================================================================
# _replace_or_insert_narrative_fields — frontmatter surgery
# ===========================================================================
class TestReplaceOrInsertNarrativeFields:
    def _make_voice(self) -> VoiceText:
        return VoiceText(
            markdown="body", provider="local",
            model="qwen2.5:7b", generated_at="2026-04-30 12:00:00Z",
        )

    def test_replaces_existing_fields(self):
        text = (
            "---\n"
            "dna: abc\n"
            "narrative_provider: \"old_provider\"\n"
            "narrative_model: \"old_model\"\n"
            "narrative_generated_at: \"2020-01-01 00:00:00Z\"\n"
            "constitution_file: x.yaml\n"
            "---\n"
            "body content\n"
        )
        out = _replace_or_insert_narrative_fields(text, self._make_voice())
        assert 'narrative_provider: "local"' in out
        assert 'narrative_model: "qwen2.5:7b"' in out
        assert 'narrative_generated_at: "2026-04-30 12:00:00Z"' in out
        assert "old_provider" not in out
        assert "old_model" not in out

    def test_inserts_after_constitution_file_when_absent(self):
        text = (
            "---\n"
            "dna: abc\n"
            "constitution_file: x.yaml\n"
            "---\n"
            "body content\n"
        )
        out = _replace_or_insert_narrative_fields(text, self._make_voice())
        # The 3 narrative_* lines should appear right after constitution_file.
        idx_cf = out.index("constitution_file: x.yaml")
        idx_np = out.index('narrative_provider: "local"')
        assert idx_np > idx_cf
        # And before the closing fence
        idx_close = out.index("---", idx_np)
        assert idx_close > idx_np

    def test_appends_when_no_constitution_file_line(self):
        """Legacy path — frontmatter without constitution_file. The
        narrative_* lines should be appended before the closing fence."""
        text = (
            "---\n"
            "dna: abc\n"
            "---\n"
            "body\n"
        )
        out = _replace_or_insert_narrative_fields(text, self._make_voice())
        assert 'narrative_provider: "local"' in out
        # All 3 lines present.
        assert 'narrative_model: "qwen2.5:7b"' in out
        assert 'narrative_generated_at: "2026-04-30 12:00:00Z"' in out

    def test_no_frontmatter_returns_unchanged(self):
        text = "no frontmatter here, just markdown body\n"
        out = _replace_or_insert_narrative_fields(text, self._make_voice())
        assert out == text


# ===========================================================================
# _replace_or_insert_voice_section — body section surgery
# ===========================================================================
class TestReplaceOrInsertVoiceSection:
    def test_replaces_existing_voice_section(self):
        text = (
            "---\nfm: 1\n---\n"
            "## Identity\n\nbody1\n\n"
            "## Voice\n\nold voice content\n\n"
            "## Lineage\n\nbody3\n"
        )
        out = _replace_or_insert_voice_section(text, "new voice content")
        assert "old voice content" not in out
        assert "new voice content" in out
        # Other sections preserved.
        assert "## Identity" in out
        assert "## Lineage" in out

    def test_inserts_when_voice_section_absent(self):
        text = (
            "---\nfm: 1\n---\n"
            "## Identity\n\nbody1\n\n"
            "## Lineage\n\nbody3\n"
        )
        out = _replace_or_insert_voice_section(text, "freshly rendered voice")
        assert "## Voice" in out
        assert "freshly rendered voice" in out

    def test_handles_no_h2_headings_appends(self):
        text = "---\nfm: 1\n---\nplain body with no h2 headings\n"
        out = _replace_or_insert_voice_section(text, "new voice")
        assert "## Voice" in out
        assert "new voice" in out


# ===========================================================================
# update_soul_voice — round-trip file update
# ===========================================================================
class TestUpdateSoulVoice:
    def test_round_trip_replaces_voice_block(self, tmp_path):
        soul = tmp_path / "agent.soul.md"
        soul.write_text(
            "---\n"
            "dna: abc\n"
            "narrative_provider: \"old\"\n"
            "narrative_model: \"old\"\n"
            "narrative_generated_at: \"2020-01-01 00:00:00Z\"\n"
            "constitution_file: x.yaml\n"
            "---\n"
            "## Identity\n\nidentity body\n\n"
            "## Voice\n\nold voice\n\n"
            "## Lineage\n\nlineage body\n",
            encoding="utf-8",
        )
        v = VoiceText(
            markdown="brand new voice paragraph",
            provider="frontier",
            model="claude-X",
            generated_at="2026-04-30 12:00:00Z",
        )
        update_soul_voice(soul, v)
        out = soul.read_text(encoding="utf-8")
        # New narrative_* fields written:
        assert 'narrative_provider: "frontier"' in out
        assert 'narrative_model: "claude-X"' in out
        # New voice section body:
        assert "brand new voice paragraph" in out
        assert "old voice" not in out
        # Other sections survived:
        assert "## Identity" in out
        assert "identity body" in out
        assert "## Lineage" in out
        assert "lineage body" in out
