"""B454/B455 (ADR-0066 T1/T2) — SOAR playbook DSL + PlaybookEngine.

T1 ships the playbook YAML parser + PlaybookDef/PlaybookStep/
PlaybookTrigger dataclasses. T2 ships the PlaybookEngine — trigger
resolution, per-target cooldown, approval gating, and
`playbook_executed` audit emission.

This file covers both:
  - the parser accepts the ADR-0066 reference playbook and rejects
    every malformed shape with a clear error
  - approval disposition resolves per ADR-0066 D2 (default-deny)
  - the engine matches triggers, honours severity + cooldown, and
    emits a `playbook_executed` entry carrying the ADR-0066 D5 shape
"""
from __future__ import annotations

import textwrap
from typing import Any

import pytest

from forest_soul_forge.security.playbook import (
    PlaybookEngine,
    PlaybookError,
    parse_playbook,
    parse_playbooks_from_dir,
    playbook_version_hash,
)


# ADR-0066 D5 — the event_data keys section-08 asserts on every
# playbook_executed chain entry.
D5_REQUIRED_KEYS = {
    "playbook_id", "playbook_version", "trigger_detection_id",
    "steps", "outcome",
}

# The ADR-0066 reference playbook, verbatim from the Decision
# section's DSL example.
ADR_EXAMPLE = textwrap.dedent("""
    playbook_id: isolate-and-collect-forensics
    version: '1'
    trigger:
      detection_rule_ids: [proc_spawn_suspicious]
      min_severity: high
      cooldown_seconds: 300

    approval:
      default: required_human
      steps_auto_approved:
        - collect_forensics

    steps:
      - id: collect_forensics
        action: archive_evidence
        args:
          artifact_path: "${detection.evidence.process_image_path}"
          transition_type: acquire
          attestor_reason: "playbook ${playbook_id} fired on ${detection.rule_id}"

      - id: isolate_process
        action: isolate_process
        args:
          pid: "${detection.evidence.pid}"
        requires_human_approval: true

      - id: notify_operator
        action: delegate
        args:
          to: operator
          message: "Playbook ${playbook_id} done"
        requires_human_approval: false

    postconditions:
      audit_event_type: playbook_executed
""").strip()


def _playbook(
    *,
    pid: str = "pb-test",
    rule_ids: str = "[proc_spawn_suspicious]",
    min_severity: str = "high",
    cooldown: int = 300,
) -> str:
    return textwrap.dedent(f"""
        playbook_id: {pid}
        version: '1'
        trigger:
          detection_rule_ids: {rule_ids}
          min_severity: {min_severity}
          cooldown_seconds: {cooldown}
        approval:
          default: required_human
          steps_auto_approved:
            - notify
        steps:
          - id: notify
            action: delegate
            args:
              message: "fired on ${{detection.rule_id}}"
    """).strip()


def _detection(
    *,
    rule_id: str = "proc_spawn_suspicious",
    severity: str = "high",
    matched: list[str] | None = None,
    batch_id: str = "batch-1",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_version": "deadbeef" * 8,
        "batch_id": batch_id,
        "technique": "attack.T1059",
        "severity": severity,
        "matched_event_ids": matched if matched is not None else ["ev-1"],
        "match_count": len(matched) if matched else 1,
    }


class _StubChain:
    """Minimal append-only chain stub — mirrors test_b392's stub,
    extended with tail() so poll_chain can be exercised."""

    def __init__(self) -> None:
        self.appended: list[Any] = []
        self._seq = 7000

    def append(self, event_type, event_data, agent_dna=None):
        self._seq += 1
        entry = type("Entry", (), {
            "seq": self._seq, "event_type": event_type,
            "event_data": event_data, "agent_dna": agent_dna,
        })()
        self.appended.append(entry)
        return entry

    def tail(self, n: int) -> list[Any]:
        return list(reversed(self.appended[-n:])) if n > 0 else []


# ---- parser: acceptance --------------------------------------------------


def test_adr_reference_playbook_parses():
    """The ADR-0066 Decision-section example must parse clean."""
    pb = parse_playbook(ADR_EXAMPLE)
    assert pb.playbook_id == "isolate-and-collect-forensics"
    assert pb.version == "1"
    assert pb.trigger.detection_rule_ids == ("proc_spawn_suspicious",)
    assert pb.trigger.min_severity == "high"
    assert pb.trigger.cooldown_seconds == 300
    assert len(pb.steps) == 3
    assert pb.postcondition_audit_event_type == "playbook_executed"


def test_approval_resolution_default_deny():
    """ADR-0066 D2 — a step is approval-gated UNLESS explicitly
    opted out, via the allowlist OR a per-step false."""
    pb = parse_playbook(ADR_EXAMPLE)
    by_id = {s.id: s for s in pb.steps}
    # in steps_auto_approved → no approval
    assert by_id["collect_forensics"].requires_approval is False
    # requires_human_approval: true → approval
    assert by_id["isolate_process"].requires_approval is True
    # requires_human_approval: false → no approval
    assert by_id["notify_operator"].requires_approval is False
    assert pb.auto_approved_step_ids == ("collect_forensics", "notify_operator")


def test_version_hash_ignores_whitespace():
    """playbook_version pins the canonicalised body — reformatting
    must not flip it (ADR-0066 D5)."""
    a = parse_playbook(ADR_EXAMPLE)
    b = parse_playbook(ADR_EXAMPLE + "\n\n\n")
    assert a.playbook_version == b.playbook_version
    assert len(a.playbook_version) == 64
    assert len(playbook_version_hash("anything")) == 64


def test_unquoted_integer_version_coerced():
    body = _playbook().replace("version: '1'", "version: 1")
    assert parse_playbook(body).version == "1"


# ---- parser: rejection ---------------------------------------------------


def test_missing_playbook_id_rejected():
    with pytest.raises(PlaybookError, match="playbook_id"):
        parse_playbook("version: '1'\ntrigger: {}\nsteps: []")


def test_empty_body_rejected():
    with pytest.raises(PlaybookError, match="empty"):
        parse_playbook("   ")


def test_missing_trigger_rejected():
    with pytest.raises(PlaybookError, match="trigger"):
        parse_playbook("playbook_id: x\nversion: '1'\nsteps: []")


def test_empty_detection_rule_ids_rejected():
    body = _playbook(rule_ids="[]")
    with pytest.raises(PlaybookError, match="detection_rule_ids"):
        parse_playbook(body)


def test_unknown_severity_rejected():
    body = _playbook(min_severity="apocalyptic")
    with pytest.raises(PlaybookError, match="severity"):
        parse_playbook(body)


def test_negative_cooldown_rejected():
    body = _playbook(cooldown=-5)
    with pytest.raises(PlaybookError, match="cooldown_seconds"):
        parse_playbook(body)


def test_empty_steps_rejected():
    body = textwrap.dedent("""
        playbook_id: x
        version: '1'
        trigger:
          detection_rule_ids: [r]
          min_severity: low
          cooldown_seconds: 0
        steps: []
    """).strip()
    with pytest.raises(PlaybookError, match="steps"):
        parse_playbook(body)


def test_control_flow_key_rejected():
    """ADR-0066 D1 — no branches, no loops. A step carrying a
    `condition` / `loop` key must be rejected, not silently run."""
    body = textwrap.dedent("""
        playbook_id: looper
        version: '1'
        trigger:
          detection_rule_ids: [r]
          min_severity: low
          cooldown_seconds: 0
        steps:
          - id: looped
            action: delegate
            loop: 5
    """).strip()
    with pytest.raises(PlaybookError, match="unsupported key"):
        parse_playbook(body)


def test_auto_approve_unknown_step_rejected():
    # The allowlist entry `    - notify` becomes `    - ghost_step`,
    # which names no declared step (the step id stays `- id: notify`).
    body = _playbook().replace("    - notify", "    - ghost_step")
    with pytest.raises(PlaybookError, match="not a declared step"):
        parse_playbook(body)


def test_approval_conflict_rejected():
    """A step in steps_auto_approved that also sets
    requires_human_approval: true is a contradiction."""
    body = textwrap.dedent("""
        playbook_id: conflict-pb
        version: '1'
        trigger:
          detection_rule_ids: [r]
          min_severity: low
          cooldown_seconds: 0
        approval:
          default: required_human
          steps_auto_approved:
            - conflicted
        steps:
          - id: conflicted
            action: isolate_process
            requires_human_approval: true
    """).strip()
    with pytest.raises(PlaybookError, match="contradict"):
        parse_playbook(body)


def test_duplicate_playbook_id_in_dir(tmp_path):
    (tmp_path / "a.yml").write_text(_playbook(pid="dup"), encoding="utf-8")
    (tmp_path / "b.yml").write_text(_playbook(pid="dup"), encoding="utf-8")
    parsed, failed = parse_playbooks_from_dir(tmp_path)
    assert any("duplicate playbook_id" in str(e) for _, e in failed)


# ---- engine: trigger resolution -----------------------------------------


def test_engine_fires_on_matching_detection():
    engine = PlaybookEngine(playbooks=[parse_playbook(ADR_EXAMPLE)])
    chain = _StubChain()
    result = engine.process_detection(
        _detection(), detection_seq=42, audit_chain=chain,
    )
    assert result.playbooks_matched == 1
    assert len(result.runs) == 1
    fired = [e for e in chain.appended if e.event_type == "playbook_executed"]
    assert len(fired) == 1
    assert D5_REQUIRED_KEYS.issubset(fired[0].event_data.keys())
    assert fired[0].event_data["playbook_id"] == "isolate-and-collect-forensics"
    assert fired[0].event_data["trigger_detection_id"] == 42


def test_engine_severity_gate():
    """A medium detection must not fire a min_severity: high playbook."""
    engine = PlaybookEngine(playbooks=[parse_playbook(ADR_EXAMPLE)])
    result = engine.process_detection(_detection(severity="medium"))
    assert result.playbooks_matched == 0
    assert result.runs == ()


def test_engine_rule_id_gate():
    engine = PlaybookEngine(playbooks=[parse_playbook(ADR_EXAMPLE)])
    result = engine.process_detection(_detection(rule_id="something_else"))
    assert result.playbooks_matched == 0


def test_engine_outcome_approval_pending():
    """The ADR example has an approval-gated step → approval_pending."""
    engine = PlaybookEngine(playbooks=[parse_playbook(ADR_EXAMPLE)])
    result = engine.process_detection(_detection())
    assert result.runs[0].outcome == "approval_pending"


def test_engine_outcome_completed_when_all_auto():
    """A playbook whose only step is auto-approved → completed."""
    engine = PlaybookEngine(playbooks=[parse_playbook(_playbook())])
    result = engine.process_detection(_detection())
    assert result.runs[0].outcome == "completed"
    assert result.runs[0].steps[0].approval_state == "auto_approved"


# ---- engine: cooldown ----------------------------------------------------


def test_cooldown_suppresses_refire():
    engine = PlaybookEngine(playbooks=[parse_playbook(_playbook(cooldown=300))])
    d = _detection(matched=["ev-same"])
    r1 = engine.process_detection(d, now=1000.0)
    r2 = engine.process_detection(d, now=1100.0)   # within 300s
    assert len(r1.runs) == 1
    assert r2.runs == ()
    assert r2.cooldown_skipped == ("pb-test",)


def test_cooldown_different_target_not_blocked():
    engine = PlaybookEngine(playbooks=[parse_playbook(_playbook(cooldown=300))])
    r1 = engine.process_detection(_detection(matched=["ev-a"]), now=1000.0)
    r2 = engine.process_detection(_detection(matched=["ev-b"]), now=1100.0)
    assert len(r1.runs) == 1
    assert len(r2.runs) == 1   # different target_entity → no cooldown


def test_cooldown_expires():
    engine = PlaybookEngine(playbooks=[parse_playbook(_playbook(cooldown=300))])
    d = _detection(matched=["ev-same"])
    engine.process_detection(d, now=1000.0)
    r2 = engine.process_detection(d, now=1400.0)   # 400s later
    assert len(r2.runs) == 1


# ---- engine: interpolation + readiness + poll ---------------------------


def test_interpolation_resolves_detection_fields():
    engine = PlaybookEngine(playbooks=[parse_playbook(_playbook())])
    result = engine.process_detection(_detection(rule_id="proc_spawn_suspicious"))
    args = result.runs[0].steps[0].resolved_args
    assert args["message"] == "fired on proc_spawn_suspicious"


def test_engine_not_ready_blocks(tmp_path):
    (tmp_path / "bad.yml").write_text("playbook_id: x\n", encoding="utf-8")
    engine = PlaybookEngine(playbooks_dir=tmp_path)
    assert engine.ready() is False
    result = engine.process_detection(_detection())
    assert result.playbooks_matched == 0


def test_poll_chain_processes_detection_fired_events():
    engine = PlaybookEngine(playbooks=[parse_playbook(ADR_EXAMPLE)])
    chain = _StubChain()
    # Seed the chain with a detection_fired the engine should pick up.
    chain.append("detection_fired", _detection())
    chain.append("tool_call_dispatched", {"noise": True})
    high_water, results = engine.poll_chain(chain, since_seq=0)
    assert high_water > 0
    assert len(results) == 1
    assert results[0].playbooks_matched == 1
    # A second poll from the new high-water mark finds nothing new.
    hw2, results2 = engine.poll_chain(chain, since_seq=high_water)
    assert results2 == []
