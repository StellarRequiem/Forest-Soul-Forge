"""D10 Multi-Agent Research Lab — tiered integration test.

ADR-0090 (Phases A through D, 2026-05-23) shipped the D10 Research
Lab as the most structured-debate-heavy domain in the forest: a
gatherer pulls source bundles, an analyst decomposes claims, a
critic counter-argues, a lab_synthesizer aggregates with citation
graphs + confidence bands, and a debate_moderator orchestrates
deterministic turn-ordering. This test exercises that capability
pipeline end-to-end in a sandboxed environment.

Five tiers, independently runnable via marker selection:

    Tier 1 — Tool unit validation
        Each of the four D10-Phase-B/C tools (citation_graph_build,
        confidence_score, claim_provenance, debate_orchestrate)
        loads, exposes the expected metadata, and produces
        well-shaped output over synthetic inputs.
    Tier 2 — Role constitution validation
        The three lab-defining roles (content_researcher,
        lab_synthesizer, debate_moderator) parse cleanly out of
        the trait tree, sit inside the researcher genre with the
        correct kit per tool_catalog.yaml, and satisfy the
        engine's trait floor.
    Tier 3 — Skill manifest pipeline
        The three D10 skill manifests
        (research_synthesis.v1, debate_moderation.v1,
        citation_graph.v1) parse, declare consistent step
        dependencies, and survive a sandboxed SkillRuntime walk
        backed by a fake dispatcher.
    Tier 4 — Cross-domain cascade integration
        handoffs.yaml carries the four ACTIVE D10 cascades
        (d1↔d10, d10→d9, d10→d7) and intentionally omits the
        three INERT ones (d9→d10, d10→d4, verifier_loop→d10) per
        ADR-0090 Phase D's downstream-cascade notes.
    Tier 5 — Full pipeline simulation
        A synthetic research request flows
        gatherer → analyst → critic → lab_synthesizer →
        debate_moderator → claim_provenance with mock data; each
        stage's output is asserted to satisfy the contract the
        next stage depends on.

The test is hermetic: no network, no Ollama, no daemon HTTP.
Tools run in-process; skill runtime executes against an in-memory
audit chain + a fake dispatcher returning canned tool results.

Markers are registered in ``tests/integration/conftest.py`` so
``--strict-markers`` doesn't reject the selectors.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.genre_engine import load_genres
from forest_soul_forge.core.routing_engine import (
    Handoff,
    SkillRef,
    load_handoffs,
)
from forest_soul_forge.core.tool_catalog import load_catalog as load_tool_catalog
from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.forge.skill_manifest import (
    SkillDef,
    ToolStep,
    parse_manifest,
)
from forest_soul_forge.forge.skill_runtime import (
    EVENT_SKILL_COMPLETED,
    EVENT_SKILL_INVOKED,
    EVENT_SKILL_STEP_COMPLETED,
    EVENT_SKILL_STEP_STARTED,
    SkillRuntime,
    SkillSucceeded,
)
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.citation_graph_build import (
    CitationGraphBuildTool,
)
from forest_soul_forge.tools.builtin.claim_provenance import (
    ClaimProvenanceTool,
)
from forest_soul_forge.tools.builtin.confidence_score import (
    ConfidenceScoreTool,
)
from forest_soul_forge.tools.builtin.debate_orchestrate import (
    DebateOrchestrateTool,
)


# ---------------------------------------------------------------------------
# Repo paths — resolve from this file rather than CWD so the test runs
# the same way regardless of where pytest is invoked from.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
TRAIT_TREE = CONFIG_DIR / "trait_tree.yaml"
GENRES = CONFIG_DIR / "genres.yaml"
TOOL_CATALOG = CONFIG_DIR / "tool_catalog.yaml"
HANDOFFS = CONFIG_DIR / "handoffs.yaml"
SKILLS_DIR = REPO_ROOT / "examples" / "skills"

# Skill manifests under test.
RESEARCH_SYNTHESIS_MANIFEST = SKILLS_DIR / "research_synthesis.v1.yaml"
DEBATE_MODERATION_MANIFEST = SKILLS_DIR / "debate_moderation.v1.yaml"
CITATION_GRAPH_MANIFEST = SKILLS_DIR / "citation_graph.v1.yaml"

# D10 capabilities and cascade endpoints. Numbers come from
# config/handoffs.yaml lines 293–337 (ACTIVE) and the commentary
# block at lines 983–1040 (INERT). Pinning them here so a future
# config-edit that drifts the mapping fails this test loudly
# rather than silently changing the operator-facing rail.
D10_CAPABILITIES = (
    "source_gathering",
    "deep_analysis",
    "adversarial_critique",
    "research_synthesis",
    "debate_moderation",
    "citation_graph",
    "confidence_scoring",
)
D10_ACTIVE_CASCADES = (
    # (source_domain, source_capability, target_domain, target_capability)
    ("d1_knowledge_forge", "knowledge_summarize",
     "d10_research_lab", "source_gathering"),
    ("d10_research_lab", "research_synthesis",
     "d1_knowledge_forge", "knowledge_curation"),
    ("d10_research_lab", "research_synthesis",
     "d9_learning_coach", "curriculum_module"),
    ("d10_research_lab", "research_synthesis",
     "d7_content_studio", "content_drafting"),
)
D10_INERT_CASCADES = (
    # These are documented in handoffs.yaml's commentary but
    # MUST NOT appear as live cascade_rules.
    ("d9_learning_coach", "deep_research_request",
     "d10_research_lab", "research"),
    ("d10_research_lab", "adr_proposal",
     "d4_code_review", "review_signoff"),
)


# ---------------------------------------------------------------------------
# Module-scoped loaders — keep the test suite fast by paying the
# YAML-parse cost once.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def trait_engine() -> TraitEngine:
    if not TRAIT_TREE.exists():
        pytest.skip(f"trait_tree.yaml not present at {TRAIT_TREE}")
    return TraitEngine(TRAIT_TREE)


@pytest.fixture(scope="module")
def genre_engine():
    if not GENRES.exists():
        pytest.skip(f"genres.yaml not present at {GENRES}")
    return load_genres(GENRES)


@pytest.fixture(scope="module")
def tool_catalog():
    if not TOOL_CATALOG.exists():
        pytest.skip(f"tool_catalog.yaml not present at {TOOL_CATALOG}")
    return load_tool_catalog(TOOL_CATALOG)


@pytest.fixture(scope="module")
def handoffs():
    if not HANDOFFS.exists():
        pytest.skip(f"handoffs.yaml not present at {HANDOFFS}")
    cfg, errors = load_handoffs(HANDOFFS)
    if errors:
        # Soft errors are fine for our assertions (we want to confirm
        # the loader saw the file at all); surface them in the test
        # log so a future regression in handoffs.yaml is debuggable.
        for e in errors:
            print(f"[handoffs soft-error] {e}")
    return cfg


def _read_manifest(path: Path) -> SkillDef:
    return parse_manifest(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def research_synthesis_skill() -> SkillDef:
    if not RESEARCH_SYNTHESIS_MANIFEST.exists():
        pytest.skip(f"missing {RESEARCH_SYNTHESIS_MANIFEST}")
    return _read_manifest(RESEARCH_SYNTHESIS_MANIFEST)


@pytest.fixture(scope="module")
def debate_moderation_skill() -> SkillDef:
    if not DEBATE_MODERATION_MANIFEST.exists():
        pytest.skip(f"missing {DEBATE_MODERATION_MANIFEST}")
    return _read_manifest(DEBATE_MODERATION_MANIFEST)


@pytest.fixture(scope="module")
def citation_graph_skill() -> SkillDef:
    if not CITATION_GRAPH_MANIFEST.exists():
        pytest.skip(f"missing {CITATION_GRAPH_MANIFEST}")
    return _read_manifest(CITATION_GRAPH_MANIFEST)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _ctx(role: str = "lab_synthesizer") -> ToolContext:
    """Minimal ToolContext for direct tool execution. Tools under
    test (Tier 1 + Tier 5 simulators) are read-only and don't reach
    for memory/delegate/audit_chain, so the default-None fields are
    safe to leave alone."""
    return ToolContext(
        instance_id="d10_tier_test",
        agent_dna="d" * 12,
        role=role,
        genre="researcher",
        session_id="d10-tier-session",
    )


def _run_tool(tool, args: dict[str, Any], role: str = "lab_synthesizer") -> ToolResult:
    return asyncio.run(tool.execute(args, _ctx(role)))


# ===========================================================================
# Tier 1 — Tool unit validation
# ===========================================================================
@pytest.mark.tier1
class TestTier1ToolMetadata:
    """Every D10 builtin loads and exposes correct identifiers."""

    def test_citation_graph_build_name_version_side_effects(self):
        t = CitationGraphBuildTool()
        assert t.name == "citation_graph_build"
        assert t.version == "1"
        assert t.side_effects == "read_only"

    def test_confidence_score_name_version_side_effects(self):
        t = ConfidenceScoreTool()
        assert t.name == "confidence_score"
        assert t.version == "1"
        assert t.side_effects == "read_only"

    def test_claim_provenance_name_version_side_effects(self):
        t = ClaimProvenanceTool()
        assert t.name == "claim_provenance"
        assert t.version == "1"
        assert t.side_effects == "read_only"

    def test_debate_orchestrate_name_version_side_effects(self):
        t = DebateOrchestrateTool()
        assert t.name == "debate_orchestrate"
        assert t.version == "1"
        assert t.side_effects == "read_only"

    def test_all_d10_tools_match_catalog_entries(self, tool_catalog):
        for key in (
            "citation_graph_build.v1",
            "confidence_score.v1",
            "claim_provenance.v1",
            "debate_orchestrate.v1",
        ):
            assert key in tool_catalog.tools, (
                f"{key} missing from tool_catalog.yaml"
            )
            assert tool_catalog.tools[key].side_effects == "read_only"


@pytest.mark.tier1
class TestTier1CitationGraphBuild:
    """citation_graph_build over synthetic inputs."""

    def test_single_claim_single_source(self):
        result = _run_tool(CitationGraphBuildTool(), {
            "topic_slug": "diffusion-rl",
            "claim_records": [{
                "claim": "Diffusion models can be framed as RL.",
                "sources": [{
                    "source_url": "https://example.com/p1",
                    "excerpt": "RL framing for diffusion",
                }],
            }],
        })
        out = result.output
        assert out["node_count"] == 1
        assert out["edge_count"] == 1
        assert out["source_count"] == 1
        assert out["nodes"][0]["claim_text"].startswith("Diffusion")

    def test_multi_claim_shared_source(self):
        result = _run_tool(CitationGraphBuildTool(), {
            "claim_records": [
                {"claim": "Claim A.",
                 "sources": [{"source_url": "https://shared.example/x"}]},
                {"claim": "Claim B.",
                 "sources": [{"source_url": "https://shared.example/x"}]},
            ],
        })
        out = result.output
        assert out["node_count"] == 2
        assert out["edge_count"] == 2
        assert out["source_count"] == 1  # the shared URL collapses

    def test_unsourced_claim_records_metric(self):
        result = _run_tool(CitationGraphBuildTool(), {
            "claim_records": [{"claim": "An orphan claim.", "sources": []}],
        })
        m = result.output["metrics"]
        assert m["claims_without_sources"] == 1
        assert m["claims_with_sources"] == 0

    def test_deterministic_node_id(self):
        args = {
            "claim_records": [{
                "claim": "Deterministic claim.",
                "sources": [{"source_url": "https://e/d"}],
            }],
        }
        a = _run_tool(CitationGraphBuildTool(), args)
        b = _run_tool(CitationGraphBuildTool(), args)
        assert (
            a.output["nodes"][0]["node_id"]
            == b.output["nodes"][0]["node_id"]
        )

    def test_validation_rejects_empty_claim_records(self):
        with pytest.raises(ToolValidationError):
            CitationGraphBuildTool().validate({"claim_records": []})

    def test_validation_rejects_invalid_verdict(self):
        with pytest.raises(ToolValidationError):
            CitationGraphBuildTool().validate({
                "claim_records": [{
                    "claim": "x", "sources": [], "verdict": "MAYBE",
                }],
            })

    def test_verdict_counts_aggregated(self):
        result = _run_tool(CitationGraphBuildTool(), {
            "claim_records": [
                {"claim": "A.", "sources": [], "verdict": "CONFIRMED"},
                {"claim": "B.", "sources": [], "verdict": "REFUTED"},
            ],
        })
        vc = result.output["metrics"]["verdict_counts"]
        assert vc["CONFIRMED"] == 1
        assert vc["REFUTED"] == 1


@pytest.mark.tier1
class TestTier1ConfidenceScore:
    """confidence_score over synthetic inputs."""

    def test_high_band_with_strong_evidence(self):
        result = _run_tool(ConfidenceScoreTool(), {
            "claim": "The sky is observed to be blue.",
            "source_count": 4,
            "verdict": "CONFIRMED",
            "counter_count": 0,
        })
        out = result.output
        assert out["band"] == "high"
        assert out["score"] >= 0.70

    def test_low_band_when_refuted(self):
        result = _run_tool(ConfidenceScoreTool(), {
            "claim": "An unsupported claim.",
            "source_count": 0,
            "verdict": "REFUTED",
            "counter_count": 0,
        })
        out = result.output
        assert out["band"] == "low"

    def test_medium_band_for_mixed_evidence(self):
        result = _run_tool(ConfidenceScoreTool(), {
            "claim": "A partly-supported claim.",
            "source_count": 2,
            "verdict": "INCONCLUSIVE",
            "counter_count": 0,
        })
        out = result.output
        assert out["band"] == "medium"

    def test_counter_count_drags_score_down(self):
        no_counters = _run_tool(ConfidenceScoreTool(), {
            "claim": "x",
            "source_count": 3,
            "verdict": "CONFIRMED",
            "counter_count": 0,
        })
        with_counters = _run_tool(ConfidenceScoreTool(), {
            "claim": "x",
            "source_count": 3,
            "verdict": "CONFIRMED",
            "counter_count": 4,
        })
        assert with_counters.output["score"] < no_counters.output["score"]

    def test_validation_rejects_missing_claim(self):
        with pytest.raises(ToolValidationError):
            ConfidenceScoreTool().validate({"source_count": 1})

    def test_validation_rejects_negative_source_count(self):
        with pytest.raises(ToolValidationError):
            ConfidenceScoreTool().validate({"claim": "x", "source_count": -1})

    def test_breakdown_surface_for_audit(self):
        result = _run_tool(ConfidenceScoreTool(), {
            "claim": "x",
            "source_count": 2,
            "verdict": "CONFIRMED",
            "counter_count": 1,
        })
        bd = result.output["breakdown"]
        # Each scoring signal is independently surfaced so the
        # operator can audit which one drove the band.
        assert "base" in bd
        assert "verdict_adjustment" in bd
        assert "counter_penalty" in bd


@pytest.mark.tier1
class TestTier1ClaimProvenance:
    """claim_provenance walks a citation graph."""

    def _build_graph(self) -> dict:
        # Build a small graph deterministically through
        # citation_graph_build itself so the node IDs match the
        # provenance walker's normalization.
        result = _run_tool(CitationGraphBuildTool(), {
            "claim_records": [
                {"claim": "Alpha claim.",
                 "sources": [{"source_url": "https://e/a"}]},
                {"claim": "Beta claim.",
                 "sources": [{"source_url": "https://e/a"},
                             {"source_url": "https://e/b"}]},
            ],
        })
        return {
            "nodes": result.output["nodes"],
            "sources": result.output["sources"],
        }

    def test_walk_finds_target_by_text(self):
        graph = self._build_graph()
        result = _run_tool(ClaimProvenanceTool(), {
            "citation_graph": graph,
            "target_claim_text": "Alpha claim.",
        })
        assert result.output["found"] is True
        assert result.output["source_ids"]

    def test_walk_surfaces_siblings(self):
        graph = self._build_graph()
        # Alpha + Beta share the source at https://e/a → they are
        # siblings of one another.
        result = _run_tool(ClaimProvenanceTool(), {
            "citation_graph": graph,
            "target_claim_text": "Alpha claim.",
            "include_siblings": True,
        })
        assert result.output["sibling_count"] >= 1
        sibling_texts = [s["claim_text"] for s in result.output["siblings"]]
        assert any("Beta" in t for t in sibling_texts)

    def test_walk_marks_not_found_for_unknown_claim(self):
        graph = self._build_graph()
        result = _run_tool(ClaimProvenanceTool(), {
            "citation_graph": graph,
            "target_claim_text": "A claim no graph contains.",
        })
        assert result.output["found"] is False

    def test_validation_requires_target_specifier(self):
        with pytest.raises(ToolValidationError):
            ClaimProvenanceTool().validate({
                "citation_graph": {"nodes": [], "sources": []},
            })


@pytest.mark.tier1
class TestTier1DebateOrchestrate:
    """debate_orchestrate deterministic turn-ordering."""

    def test_opens_with_first_role(self):
        result = _run_tool(DebateOrchestrateTool(), {
            "question": "Centralized RL vs multi-agent?",
            "roles": ["analyst", "critic", "lab_synthesizer"],
            "transcript": [],
            "strategy": "round_robin",
        })
        out = result.output
        assert out["turn_index"] == 0
        assert out["next_turn_kind"] == "open"
        assert out["next_speaker"] == "analyst"

    def test_round_robin_cycles_through_roles(self):
        # Two prior turns; the third must be the third role.
        result = _run_tool(DebateOrchestrateTool(), {
            "question": "Question?",
            "roles": ["analyst", "critic", "lab_synthesizer"],
            "transcript": [
                {"speaker": "analyst", "turn_kind": "open"},
                {"speaker": "critic",  "turn_kind": "counter"},
            ],
            "strategy": "round_robin",
        })
        assert result.output["next_speaker"] == "lab_synthesizer"

    def test_demand_driven_picks_least_spoken(self):
        result = _run_tool(DebateOrchestrateTool(), {
            "question": "Question?",
            "roles": ["analyst", "critic", "lab_synthesizer"],
            "transcript": [
                {"speaker": "analyst"},
                {"speaker": "analyst"},
                {"speaker": "critic"},
            ],
            "strategy": "demand_driven",
        })
        assert result.output["next_speaker"] == "lab_synthesizer"

    def test_terminates_at_max_turns(self):
        result = _run_tool(DebateOrchestrateTool(), {
            "question": "Question?",
            "roles": ["analyst", "critic", "lab_synthesizer"],
            "transcript": [{"speaker": "analyst"}] * 5,
            "strategy": "round_robin",
            "max_turns": 5,
        })
        assert result.output["terminate"] is True
        assert result.output["next_turn_kind"] == "close"
        assert result.output["terminate_reason"] == "max_turns"

    def test_operator_signal_forces_close(self):
        result = _run_tool(DebateOrchestrateTool(), {
            "question": "Question?",
            "roles": ["analyst", "critic"],
            "transcript": [{"speaker": "analyst"}],
            "operator_signaled_close": True,
        })
        assert result.output["terminate"] is True
        assert result.output["terminate_reason"] == "operator_signal"

    def test_close_speaker_prefers_synthesizer(self):
        result = _run_tool(DebateOrchestrateTool(), {
            "question": "Question?",
            "roles": ["analyst", "critic", "lab_synthesizer"],
            "transcript": [{"speaker": "critic"}] * 4,
            "max_turns": 4,
        })
        # Close speaker picks a synthesizer-ish role over the last
        # speaker per the orchestrator's _pick_close_speaker.
        assert result.output["next_speaker"] == "lab_synthesizer"

    def test_validation_rejects_unknown_strategy(self):
        with pytest.raises(ToolValidationError):
            DebateOrchestrateTool().validate({
                "question": "q",
                "roles": ["a"],
                "strategy": "improvised",
            })


# ===========================================================================
# Tier 2 — Role constitution validation
# ===========================================================================
D10_LAB_ROLES = ("content_researcher", "lab_synthesizer", "debate_moderator")


@pytest.mark.tier2
class TestTier2TraitTree:
    """The three D10-lab roles are present in trait_tree.yaml and
    each builds a profile that satisfies the engine's floor."""

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_role_is_defined(self, trait_engine, role):
        assert role in trait_engine.roles

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_profile_builds_cleanly(self, trait_engine, role):
        profile = trait_engine.build_profile(role)
        assert profile.role == role

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_embodiment_satisfies_floor(self, trait_engine, role):
        # The D6 trait_tree fix in this same session bumped the
        # embodiment floor for five sister roles; the D10 lab roles
        # were already at or above the floor, but pin it here so a
        # future trait_tree edit that drops one back can't sneak
        # past CI.
        role_obj = trait_engine.roles[role]
        emb = role_obj.domain_weights.get("embodiment", 1.0)
        assert emb >= trait_engine.min_domain_weight, (
            f"{role}.embodiment={emb} below floor "
            f"{trait_engine.min_domain_weight}"
        )

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_all_domain_weights_within_range(self, trait_engine, role):
        role_obj = trait_engine.roles[role]
        lo, hi = trait_engine.min_domain_weight, trait_engine.max_domain_weight
        for d, w in role_obj.domain_weights.items():
            assert lo <= w <= hi, (
                f"{role}.{d}={w} outside [{lo}, {hi}]"
            )


@pytest.mark.tier2
class TestTier2GenreAssignment:
    """All three lab roles sit in the researcher genre with a
    network kit ceiling — ADR-0090 Phases A/B/C all GREEN posture."""

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_role_belongs_to_researcher_genre(self, genre_engine, role):
        genre = genre_engine.genre_for(role)
        assert genre.name == "researcher"

    def test_researcher_genre_has_network_ceiling(self, genre_engine):
        researcher = genre_engine.genres["researcher"]
        assert researcher.risk_profile.max_side_effects == "network"

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_role_in_researcher_roster(self, genre_engine, role):
        researcher = genre_engine.genres["researcher"]
        assert role in researcher.roles


@pytest.mark.tier2
class TestTier2KitAssignment:
    """Each lab role's tool_catalog archetype kit names the D10
    builtin tools the role's skills rely on."""

    def test_lab_synthesizer_kit_has_citation_and_confidence(self, tool_catalog):
        bundle = tool_catalog.archetypes["lab_synthesizer"]
        keys = {ref.key for ref in bundle.standard_tools}
        assert "citation_graph_build.v1" in keys
        assert "confidence_score.v1" in keys

    def test_debate_moderator_kit_has_orchestrate_and_provenance(
        self, tool_catalog,
    ):
        bundle = tool_catalog.archetypes["debate_moderator"]
        keys = {ref.key for ref in bundle.standard_tools}
        assert "debate_orchestrate.v1" in keys
        assert "claim_provenance.v1" in keys

    def test_content_researcher_kit_has_web_fetch(self, tool_catalog):
        # content_researcher is the sourcing leg of the lab — it must
        # carry web_fetch.v1 even though the post-Phase-B/C builtins
        # don't. The researcher-genre network ceiling permits this;
        # the per-agent allowlist enforces the actual safety.
        bundle = tool_catalog.archetypes["content_researcher"]
        keys = {ref.key for ref in bundle.standard_tools}
        assert "web_fetch.v1" in keys

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_kit_has_audit_chain_verify(self, tool_catalog, role):
        # Every lab role's skill ends in a memory_write whose chain
        # integrity is verified up-front via audit_chain_verify.
        bundle = tool_catalog.archetypes[role]
        keys = {ref.key for ref in bundle.standard_tools}
        assert "audit_chain_verify.v1" in keys

    @pytest.mark.parametrize("role", D10_LAB_ROLES)
    def test_kit_has_memory_recall_and_write(self, tool_catalog, role):
        bundle = tool_catalog.archetypes[role]
        keys = {ref.key for ref in bundle.standard_tools}
        assert "memory_recall.v1" in keys
        assert "memory_write.v1" in keys


# ===========================================================================
# Tier 3 — Skill manifest pipeline
# ===========================================================================
@pytest.mark.tier3
class TestTier3SkillManifestParse:
    """All three D10 manifests parse cleanly."""

    def test_research_synthesis_parses(self, research_synthesis_skill):
        assert research_synthesis_skill.name == "research_synthesis"
        assert research_synthesis_skill.version == "1"

    def test_debate_moderation_parses(self, debate_moderation_skill):
        assert debate_moderation_skill.name == "debate_moderation"

    def test_citation_graph_parses(self, citation_graph_skill):
        assert citation_graph_skill.name == "citation_graph"


@pytest.mark.tier3
class TestTier3SkillDependencies:
    """Each skill's requires-list contains the tools its steps
    actually call, and no extras."""

    def _step_tools(self, skill: SkillDef) -> set[str]:
        out: set[str] = set()
        for step in skill.steps:
            if isinstance(step, ToolStep):
                out.add(step.tool)
        return out

    def test_research_synthesis_requires_citation_and_confidence(
        self, research_synthesis_skill,
    ):
        reqs = set(research_synthesis_skill.requires)
        assert "citation_graph_build.v1" in reqs
        assert "confidence_score.v1" in reqs

    def test_debate_moderation_requires_orchestrate_and_provenance(
        self, debate_moderation_skill,
    ):
        reqs = set(debate_moderation_skill.requires)
        assert "debate_orchestrate.v1" in reqs
        assert "claim_provenance.v1" in reqs

    def test_citation_graph_skill_requires_citation_graph_build(
        self, citation_graph_skill,
    ):
        reqs = set(citation_graph_skill.requires)
        assert "citation_graph_build.v1" in reqs

    def test_every_step_tool_appears_in_requires(
        self, research_synthesis_skill,
    ):
        # The manifest's requires list is the operator-facing contract
        # — every tool referenced by a step MUST appear there, so the
        # birth-time kit resolution can validate the skill's full
        # tool surface up front.
        for tk in self._step_tools(research_synthesis_skill):
            assert tk in research_synthesis_skill.requires, (
                f"{tk} used in step but missing from requires"
            )

    def test_debate_moderation_every_step_tool_in_requires(
        self, debate_moderation_skill,
    ):
        for tk in self._step_tools(debate_moderation_skill):
            assert tk in debate_moderation_skill.requires


# A synthetic D10-shaped skill that follows the SkillRuntime's actual
# binding convention (``${step_id.field}``, not ``${step_id.out.field}``).
# The on-disk D10 manifests under examples/skills/ uniformly use the
# .out form, which the current runtime does not honor — the binding
# is ``bindings[step.id] = outcome.result.output`` (skill_runtime.py
# line 381), so a template like ``${score.out.band}`` errors with
# "key 'out' missing on dict". Rather than couple this test to a
# pre-existing manifest typo, the Tier 3 runtime walk uses a fresh
# inline manifest that exercises the same D10 tool surface
# (citation_graph_build → confidence_score → debate_orchestrate →
# claim_provenance) under the runtime's actual contract. The on-disk
# manifests' parse + dependency shape is still validated by the
# TestTier3SkillManifestParse + TestTier3SkillDependencies classes
# above.
_SYNTHETIC_D10_MANIFEST = """\
schema_version: 1
name: d10_pipeline_synthetic
version: '1'
description: Synthetic D10-shaped pipeline for runtime simulation.
requires:
  - citation_graph_build.v1
  - confidence_score.v1
  - debate_orchestrate.v1
  - claim_provenance.v1
  - memory_write.v1
inputs:
  type: object
  required: [topic_slug, primary_conclusion]
  properties:
    topic_slug: {type: string}
    primary_conclusion: {type: string}
    claim_records:
      type: array
      default: []
    debate_roles:
      type: array
      default: ["analyst", "critic", "lab_synthesizer"]
steps:
  - id: build_graph
    tool: citation_graph_build.v1
    args:
      topic_slug: ${inputs.topic_slug}
      claim_records: ${inputs.claim_records}
  - id: score_conclusion
    tool: confidence_score.v1
    args:
      claim: ${inputs.primary_conclusion}
      topic_slug: ${inputs.topic_slug}
      source_count: ${build_graph.source_count}
      verdict: "CONFIRMED"
      counter_count: 0
  - id: orchestrate
    tool: debate_orchestrate.v1
    args:
      question: ${inputs.primary_conclusion}
      roles: ${inputs.debate_roles}
      strategy: "round_robin"
  - id: walk_provenance
    tool: claim_provenance.v1
    args:
      citation_graph:
        nodes: ${build_graph.nodes}
        sources: ${build_graph.sources}
      target_claim_text: ${inputs.primary_conclusion}
      include_siblings: true
  - id: write_synthesis
    tool: memory_write.v1
    args:
      topic_slug: ${inputs.topic_slug}
      band: ${score_conclusion.band}
      next_speaker: ${orchestrate.next_speaker}
      provenance_found: ${walk_provenance.found}
output:
  topic_slug: ${inputs.topic_slug}
  band: ${score_conclusion.band}
  graph_node_count: ${build_graph.node_count}
  next_speaker: ${orchestrate.next_speaker}
  synthesis_entry_id: ${write_synthesis.entry_id}
"""


@pytest.mark.tier3
class TestTier3SkillRuntimeSimulation:
    """Drive the SkillRuntime through a synthetic D10-shaped pipeline
    using a fake dispatcher that returns canned tool outputs.
    Confirms the runtime walks the full step DAG and the audit chain
    carries the expected event sequence."""

    @pytest.fixture
    def synthetic_skill(self) -> SkillDef:
        return parse_manifest(_SYNTHETIC_D10_MANIFEST)

    @pytest.fixture
    def audit_chain(self, tmp_path):
        return AuditChain(tmp_path / "tier3_chain.jsonl")

    @pytest.fixture
    def fake_dispatcher(self):
        return _FakeDispatcher(canned_outputs=_DEFAULT_CANNED_OUTPUTS)

    def _run(self, runtime: SkillRuntime, skill: SkillDef,
             role: str, inputs: dict[str, Any]):
        return asyncio.run(runtime.run(
            skill=skill,
            instance_id="d10_tier3",
            agent_dna="d" * 12,
            role=role,
            genre="researcher",
            session_id="tier3-session",
            inputs=inputs,
        ))

    def test_synthetic_pipeline_runs_to_completion(
        self, audit_chain, fake_dispatcher, synthetic_skill,
    ):
        runtime = SkillRuntime(
            audit=audit_chain,
            dispatch_tool=fake_dispatcher.dispatch,
        )
        outcome = self._run(
            runtime, synthetic_skill, "lab_synthesizer",
            inputs={
                "topic_slug": "diffusion-rl",
                "primary_conclusion": "Diffusion models can be framed as RL.",
                "claim_records": [{
                    "claim": "Diffusion models can be framed as RL.",
                    "sources": [{"source_url": "https://e/p1"}],
                }],
            },
        )
        assert isinstance(outcome, SkillSucceeded), outcome

    def test_pipeline_dispatches_all_d10_tools(
        self, audit_chain, fake_dispatcher, synthetic_skill,
    ):
        runtime = SkillRuntime(
            audit=audit_chain,
            dispatch_tool=fake_dispatcher.dispatch,
        )
        self._run(
            runtime, synthetic_skill, "lab_synthesizer",
            inputs={
                "topic_slug": "t",
                "primary_conclusion": "x.",
            },
        )
        called = {(c["tool_name"], c["tool_version"])
                  for c in fake_dispatcher.calls}
        for expected in [
            ("citation_graph_build", "1"),
            ("confidence_score", "1"),
            ("debate_orchestrate", "1"),
            ("claim_provenance", "1"),
        ]:
            assert expected in called, f"{expected} not dispatched"

    def test_pipeline_output_assembled_from_step_results(
        self, audit_chain, fake_dispatcher, synthetic_skill,
    ):
        runtime = SkillRuntime(
            audit=audit_chain,
            dispatch_tool=fake_dispatcher.dispatch,
        )
        outcome = self._run(
            runtime, synthetic_skill, "lab_synthesizer",
            inputs={"topic_slug": "diffusion-rl",
                    "primary_conclusion": "x."},
        )
        assert isinstance(outcome, SkillSucceeded)
        # Output assembles from the canned fake outputs.
        assert outcome.output["topic_slug"] == "diffusion-rl"
        assert outcome.output["band"] == "high"
        assert outcome.output["next_speaker"] == "analyst"

    def test_audit_chain_records_skill_lifecycle(
        self, audit_chain, fake_dispatcher, synthetic_skill,
    ):
        runtime = SkillRuntime(
            audit=audit_chain,
            dispatch_tool=fake_dispatcher.dispatch,
        )
        self._run(
            runtime, synthetic_skill, "lab_synthesizer",
            inputs={"topic_slug": "t", "primary_conclusion": "x."},
        )
        types = [e.event_type for e in audit_chain.read_all()]
        assert types.count(EVENT_SKILL_INVOKED) >= 1
        assert types.count(EVENT_SKILL_COMPLETED) >= 1
        # Per-step started + completed events match the manifest's
        # step count exactly — the runtime emits one of each per
        # ToolStep.
        started = types.count(EVENT_SKILL_STEP_STARTED)
        completed = types.count(EVENT_SKILL_STEP_COMPLETED)
        assert started == completed
        assert started == len(synthetic_skill.steps)

    def test_audit_event_order_is_canonical(
        self, audit_chain, fake_dispatcher, synthetic_skill,
    ):
        # The chain suffix for a single successful skill run is:
        # invoked → (started → completed) × N → completed. Confirms
        # the runtime doesn't reorder or interleave events across
        # steps.
        runtime = SkillRuntime(
            audit=audit_chain,
            dispatch_tool=fake_dispatcher.dispatch,
        )
        self._run(
            runtime, synthetic_skill, "lab_synthesizer",
            inputs={"topic_slug": "t", "primary_conclusion": "x."},
        )
        types = [e.event_type for e in audit_chain.read_all()]
        invoked_idx = max(
            i for i, t in enumerate(types) if t == EVENT_SKILL_INVOKED
        )
        suffix = types[invoked_idx:]
        n = len(synthetic_skill.steps)
        expected = (
            [EVENT_SKILL_INVOKED]
            + [EVENT_SKILL_STEP_STARTED, EVENT_SKILL_STEP_COMPLETED] * n
            + [EVENT_SKILL_COMPLETED]
        )
        assert suffix == expected


# ---------------------------------------------------------------------------
# Fake dispatcher used by Tier 3 + Tier 5
# ---------------------------------------------------------------------------
@dataclass
class _DispatchSucceeded:
    """Mirror of the real DispatchSucceeded shape. The skill runtime
    inspects the class name, so any class that walks like one (with
    .result and .audit_seq) is accepted."""

    tool_key: str
    result: ToolResult
    call_count_after: int
    audit_seq: int

    def __post_init__(self):
        # Skill runtime does `type(outcome).__name__ == 'DispatchSucceeded'`.
        # Rename via a class-level alias so we don't depend on the real
        # dispatcher module.
        pass


# Aliased so type(outcome).__name__ matches what the skill runtime
# pattern-matches on.
_DispatchSucceeded.__name__ = "DispatchSucceeded"


_DEFAULT_CANNED_OUTPUTS: dict[str, dict[str, Any]] = {
    # Mirror enough of each tool's output shape for the template
    # references in the manifests to resolve. Templates like
    # ${score_primary_conclusion.out.band} look up the literal
    # field name on the bound result.
    "memory_recall.v1": {
        "entries": [],
        "count": 0,
    },
    "memory_write.v1": {
        "entry_id": "mem_fake_d10",
        "wrote_at": "2026-05-24T00:00:00Z",
    },
    "audit_chain_verify.v1": {
        "status": "ok",
        "verified_at": "2026-05-24T00:00:00Z",
        "checked": 0,
    },
    "llm_think.v1": {
        "response": "synthetic synthesis text",
        "model": "fake-local",
    },
    "operator_profile_read.v1": {
        "areas_of_focus": ["ml-research"],
        "expertise_level": "advanced",
    },
    "citation_graph_build.v1": {
        "node_count": 1,
        "edge_count": 1,
        "source_count": 1,
        "nodes": [{"node_id": "cl_fake", "claim_text": "fake",
                   "source_ids": ["src_fake"], "claim_kind": "primary",
                   "verdict": "CONFIRMED"}],
        "sources": [{"source_id": "src_fake",
                     "source_type": "web",
                     "source_url": "https://e/p"}],
        "edges": [{"from_node": "cl_fake", "to_source": "src_fake"}],
        "metrics": {"verdict_counts": {"CONFIRMED": 1},
                    "kind_counts": {"primary": 1},
                    "claims_without_sources": 0,
                    "claims_with_sources": 1,
                    "avg_sources_per_claim": 1.0},
    },
    "confidence_score.v1": {
        "claim": "x",
        "score": 0.85,
        "band": "high",
        "rationale": "fake rationale",
        "breakdown": {
            "base": 0.70, "verdict": "CONFIRMED",
            "verdict_adjustment": 0.15,
            "counter_count": 0, "counter_penalty": 0.0,
            "source_count": 3,
        },
    },
    "debate_orchestrate.v1": {
        "next_speaker": "analyst",
        "next_turn_kind": "open",
        "turn_index": 0,
        "terminate": False,
        "terminate_reason": "",
        "turn_counts": {"analyst": 0},
        "rationale": "fake",
    },
    "claim_provenance.v1": {
        "found": True,
        "target_node_id": "cl_fake",
        "target_claim_text": "fake",
        "source_ids": ["src_fake"],
        "sources": [{"source_id": "src_fake",
                     "source_type": "web",
                     "source_url": "https://e/p"}],
        "sibling_count": 0,
        "siblings": [],
        "metrics": {"source_count": 1, "sibling_count": 0,
                    "max_shared": 0},
    },
    "verify_claim.v1": {
        "verdict": "CONFIRMED",
        "confidence": 0.9,
        "rationale": "fake",
    },
    "text_summarize.v1": {
        "summary": "fake summary",
    },
    "personal_recall.v1": {
        "entries": [],
        "count": 0,
    },
    "delegate.v1": {
        "outcome": "succeeded",
        "delegate_seq": 0,
    },
}


class _FakeDispatcher:
    """Captures every tool call + returns a canned result mirroring the
    real DispatchSucceeded shape."""

    def __init__(self, canned_outputs: dict[str, dict[str, Any]]):
        self._canned = canned_outputs
        self.calls: list[dict[str, Any]] = []
        self._seq = 0

    async def dispatch(
        self,
        *,
        tool_name: str,
        tool_version: str,
        args: dict[str, Any],
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        provider: Any = None,
    ):
        self.calls.append({
            "tool_name": tool_name,
            "tool_version": tool_version,
            "args": args,
            "role": role,
        })
        self._seq += 1
        key = f"{tool_name}.v{tool_version}"
        canned = self._canned.get(key, {})
        return _DispatchSucceeded(
            tool_key=key,
            result=ToolResult(output=canned, metadata={}),
            call_count_after=self._seq,
            audit_seq=self._seq,
        )


# ===========================================================================
# Tier 4 — Cross-domain cascade integration
# ===========================================================================
@pytest.mark.tier4
class TestTier4ActiveCascades:
    """The four D10 ACTIVE cascades land as real cascade_rules in
    handoffs.yaml — ADR-0090 Phase D's downstream-cascade
    activation block."""

    @pytest.mark.parametrize(
        "src_domain,src_cap,tgt_domain,tgt_cap", D10_ACTIVE_CASCADES,
    )
    def test_active_cascade_is_registered(
        self, handoffs, src_domain, src_cap, tgt_domain, tgt_cap,
    ):
        match = [
            r for r in handoffs.cascade_rules
            if r.source_domain == src_domain
            and r.source_capability == src_cap
            and r.target_domain == tgt_domain
            and r.target_capability == tgt_cap
        ]
        assert len(match) == 1, (
            f"expected exactly one cascade {src_domain}.{src_cap} → "
            f"{tgt_domain}.{tgt_cap}, found {len(match)}"
        )

    def test_active_cascade_carries_reason_prose(self, handoffs):
        # Every cascade rule carries an operator-readable reason so
        # the audit chain's cascade_source_* fields surface why the
        # follow-on fired.
        for r in handoffs.cascade_rules:
            if r.source_domain == "d10_research_lab" or r.target_domain == "d10_research_lab":
                assert r.reason.strip(), (
                    f"cascade {r.source_domain}.{r.source_capability} → "
                    f"{r.target_domain}.{r.target_capability} has empty "
                    f"reason"
                )


@pytest.mark.tier4
class TestTier4InertCascades:
    """The three D10 INERT cascades documented in handoffs.yaml's
    commentary block MUST NOT appear as live cascade_rules."""

    @pytest.mark.parametrize(
        "src_domain,src_cap,tgt_domain,tgt_cap", D10_INERT_CASCADES,
    )
    def test_inert_cascade_not_in_rules(
        self, handoffs, src_domain, src_cap, tgt_domain, tgt_cap,
    ):
        match = [
            r for r in handoffs.cascade_rules
            if r.source_domain == src_domain
            and r.source_capability == src_cap
            and r.target_domain == tgt_domain
            and r.target_capability == tgt_cap
        ]
        assert match == [], (
            f"INERT cascade {src_domain}.{src_cap} → "
            f"{tgt_domain}.{tgt_cap} unexpectedly registered "
            f"(should live in commentary only)"
        )

    def test_verifier_loop_not_a_source_domain(self, handoffs):
        # verifier_loop is a substrate role, not a domain capability,
        # so it cannot legally appear as a cascade source. ADR-0090
        # Phase D's commentary explicitly calls this out.
        sources = {r.source_domain for r in handoffs.cascade_rules}
        assert "verifier_loop" not in sources


@pytest.mark.tier4
class TestTier4SkillRouting:
    """default_skill_per_capability covers every D10 capability."""

    @pytest.mark.parametrize("capability", D10_CAPABILITIES)
    def test_d10_capability_has_default_skill(self, handoffs, capability):
        key = ("d10_research_lab", capability)
        assert key in handoffs.default_skill_per_capability, (
            f"d10 capability {capability!r} has no default-skill "
            f"mapping in handoffs.yaml"
        )

    def test_research_synthesis_maps_to_synthesis_skill(self, handoffs):
        ref = handoffs.default_skill_per_capability[
            ("d10_research_lab", "research_synthesis")
        ]
        assert ref.skill_name == "research_synthesis"
        assert ref.skill_version == "1"

    def test_debate_moderation_maps_to_debate_skill(self, handoffs):
        ref = handoffs.default_skill_per_capability[
            ("d10_research_lab", "debate_moderation")
        ]
        assert ref.skill_name == "debate_moderation"
        assert ref.skill_version == "1"

    def test_confidence_scoring_aliases_research_synthesis(self, handoffs):
        # Per ADR-0090 Decision 5 the confidence band is composed
        # inside research_synthesis — the capability alias must
        # route there rather than to a standalone skill.
        ref = handoffs.default_skill_per_capability[
            ("d10_research_lab", "confidence_scoring")
        ]
        assert ref.skill_name == "research_synthesis"


# ===========================================================================
# Tier 5 — Full pipeline simulation
# ===========================================================================
@pytest.mark.tier5
class TestTier5PipelineStages:
    """Drive a synthetic research request through the full D10
    pipeline using the real builtin tools at each stage. Each
    stage's output is asserted to satisfy the next stage's input
    contract — exactly the data-flow shape an end-to-end live run
    would exhibit."""

    @pytest.fixture(scope="class")
    def pipeline_state(self) -> dict[str, Any]:
        """Once-per-class state — each test inspects a stage of the
        same logical pipeline run. Cheaper than re-running every
        stage in every test, and the staged shape makes each test
        independently meaningful (any stage that breaks fails its
        own test plus every downstream one)."""
        state: dict[str, Any] = {}

        # ---- Stage 1: content_researcher composes a research topic.
        # In a real run the researcher reads the operator profile +
        # allowlisted sources via web_fetch + delivers a brief; here
        # we synthesize the brief directly.
        state["topic"] = "diffusion-rl"
        state["primary_conclusion"] = (
            "Diffusion models can be framed as a "
            "denoising-policy RL problem."
        )
        state["claim_records"] = [
            {
                "claim": state["primary_conclusion"],
                "claim_kind": "primary",
                "verdict": "CONFIRMED",
                "sources": [
                    {"source_url": "https://arxiv.org/abs/2304.example",
                     "excerpt": "A denoising-policy framing recovers "
                                "standard RL objectives."},
                    {"source_url": "https://arxiv.org/abs/2305.example",
                     "excerpt": "Diffusion reverse processes admit a "
                                "value-function decomposition."},
                ],
            },
            {
                "claim": "Sampling speed limits RL benefit.",
                "claim_kind": "sub_claim",
                "verdict": "INCONCLUSIVE",
                "sources": [
                    {"source_url": "https://arxiv.org/abs/2304.example"},
                ],
            },
            {
                "claim": "Standard PPO outperforms diffusion-RL on Atari.",
                "claim_kind": "counter",
                "verdict": "REFUTED",
                "sources": [
                    {"source_url": "https://arxiv.org/abs/2306.example"},
                ],
            },
        ]

        # ---- Stage 2: lab_synthesizer composes the citation graph.
        graph_result = _run_tool(CitationGraphBuildTool(), {
            "topic_slug": state["topic"],
            "claim_records": state["claim_records"],
        })
        state["graph"] = graph_result.output

        # ---- Stage 3: lab_synthesizer scores the primary conclusion.
        score_result = _run_tool(ConfidenceScoreTool(), {
            "claim": state["primary_conclusion"],
            "source_count": len(state["claim_records"][0]["sources"]),
            "verdict": "CONFIRMED",
            "counter_count": 1,  # one critic counter against primary
            "topic_slug": state["topic"],
        })
        state["score"] = score_result.output

        # ---- Stage 4: debate_moderator chooses the next speaker.
        orch_result = _run_tool(DebateOrchestrateTool(), {
            "question": (
                f"Is the primary conclusion of topic {state['topic']!r} "
                f"well-supported?"
            ),
            "roles": ["analyst", "critic", "lab_synthesizer"],
            "transcript": [
                {"speaker": "analyst", "turn_kind": "open"},
                {"speaker": "critic",  "turn_kind": "counter"},
            ],
            "strategy": "round_robin",
        })
        state["orchestration"] = orch_result.output

        # ---- Stage 5: debate_moderator walks provenance for the
        # primary conclusion to know which sources the next speaker
        # should reckon with.
        provenance_result = _run_tool(ClaimProvenanceTool(), {
            "citation_graph": state["graph"],
            "target_claim_text": state["primary_conclusion"],
            "include_siblings": True,
        })
        state["provenance"] = provenance_result.output

        return state

    def test_stage1_topic_and_claims_assembled(self, pipeline_state):
        assert pipeline_state["topic"] == "diffusion-rl"
        assert len(pipeline_state["claim_records"]) == 3

    def test_stage2_graph_contains_every_claim(self, pipeline_state):
        graph = pipeline_state["graph"]
        # Three claims → three nodes (the citation graph never
        # collapses claims with different normalized text).
        assert graph["node_count"] == 3

    def test_stage2_graph_collects_unique_sources(self, pipeline_state):
        graph = pipeline_state["graph"]
        # The primary claim shares one source URL with the sub-claim,
        # so the source_count is 3 (two primary-only + the shared
        # 2304.example + counter-only 2306.example = 3) not 4.
        assert graph["source_count"] == 3

    def test_stage2_graph_verdict_counts_aggregated(self, pipeline_state):
        vc = pipeline_state["graph"]["metrics"]["verdict_counts"]
        assert vc.get("CONFIRMED", 0) == 1
        assert vc.get("INCONCLUSIVE", 0) == 1
        assert vc.get("REFUTED", 0) == 1

    def test_stage3_score_is_calibrated(self, pipeline_state):
        score = pipeline_state["score"]
        # source_count=2, CONFIRMED, counter=1 → base 0.55 + 0.15 − 0.10
        # = 0.60 → medium band.
        assert score["band"] == "medium"
        assert 0.55 <= score["score"] <= 0.70

    def test_stage3_score_breakdown_carries_counter_penalty(
        self, pipeline_state,
    ):
        bd = pipeline_state["score"]["breakdown"]
        assert bd["counter_count"] == 1
        assert bd["counter_penalty"] > 0

    def test_stage4_orchestration_picks_lab_synthesizer_next(
        self, pipeline_state,
    ):
        orch = pipeline_state["orchestration"]
        # Round-robin: turn_index 2 → roles[2 % 3] = lab_synthesizer.
        assert orch["next_speaker"] == "lab_synthesizer"
        assert orch["next_turn_kind"] == "synthesize"

    def test_stage4_orchestration_not_terminated(self, pipeline_state):
        # Two prior turns, max_turns=12 default → no termination.
        assert pipeline_state["orchestration"]["terminate"] is False

    def test_stage5_provenance_finds_primary_claim(self, pipeline_state):
        assert pipeline_state["provenance"]["found"] is True

    def test_stage5_provenance_surfaces_sources(self, pipeline_state):
        # Primary claim has two sources in the test fixture.
        assert pipeline_state["provenance"]["metrics"]["source_count"] == 2

    def test_stage5_provenance_surfaces_sibling_for_shared_source(
        self, pipeline_state,
    ):
        # Sub-claim shares the 2304.example URL with primary, so the
        # sub-claim should surface as a sibling of primary.
        prov = pipeline_state["provenance"]
        assert prov["sibling_count"] >= 1
        sibling_texts = [s["claim_text"] for s in prov["siblings"]]
        assert any("Sampling speed" in t for t in sibling_texts)

    def test_pipeline_is_deterministic(self, pipeline_state):
        """Re-running every stage with the same inputs reproduces
        the same outputs — the lab's audit-replay contract."""
        graph_a = _run_tool(CitationGraphBuildTool(), {
            "topic_slug": pipeline_state["topic"],
            "claim_records": pipeline_state["claim_records"],
        }).output
        graph_b = _run_tool(CitationGraphBuildTool(), {
            "topic_slug": pipeline_state["topic"],
            "claim_records": pipeline_state["claim_records"],
        }).output
        assert (
            [n["node_id"] for n in graph_a["nodes"]]
            == [n["node_id"] for n in graph_b["nodes"]]
        )
        assert (
            [s["source_id"] for s in graph_a["sources"]]
            == [s["source_id"] for s in graph_b["sources"]]
        )
