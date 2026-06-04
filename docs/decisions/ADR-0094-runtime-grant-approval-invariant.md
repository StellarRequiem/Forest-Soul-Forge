# ADR-0094 — Runtime-granted tools must honor the always-approval invariant

**Status:** Accepted (2026-06-04). Fixed at the dispatch choke point
(`tools/governance_pipeline.py::ApprovalGateStep`), with the gated side-effect
set derived from `core/tool_policy` so the two cannot drift; regression tests in
`tests/unit/test_governance_pipeline.py`.

## Context

`core/tool_policy.py` declares two **unconditional** safety rules
(`when_trait=None`, i.e. they apply to every agent regardless of trait values):

- `filesystem_always_human_approval` — *"Filesystem tools always require human
  approval … a filesystem write under agent-controlled input is a path-traversal
  risk no matter the agent's caution level."*
- `external_always_human_approval` — *"External (mutating) tools always require
  human approval … the daemon refuses to bypass approval regardless of the
  agent's trait values."*

These are applied by `resolve_constraints(profile, tool)` — which bakes
`requires_human_approval=True` into the agent's `constitution.yaml` **at birth**.
At dispatch, `ConstraintResolutionStep` then *reads* the constitution for the
resolved constraints; the `ApprovalGateStep` pends if the resolved constraint OR
the genre policy elevates.

**The bug.** `resolve_constraints` is called only at **birth** and in **preview**
— never on the dispatch path. A tool **granted at runtime**
(`POST /agents/{id}/tools/grant`, ADR-0060) is not in the birth-time
constitution; `ConstraintResolutionStep` resolves it via the grant-lookup
fallback to **catalog-default constraints**, which do not carry the unconditional
rules. So a runtime-granted filesystem or external tool reached the
`ApprovalGateStep` with `requires_human_approval=False`, and ran **without
approval** — defeating an invariant the system documents as absolute.

**How it was found.** Driving the live daemon for the Golden Demo, a default
`vault_warden` granted `misconception_log` (`side_effects=filesystem`) ran it to
completion under **green and yellow** posture. Only **red** posture stopped it,
and the audit recorded `gate_source = posture_red_grant_lower` — i.e. posture
caught it, not the tool's own approval requirement. Empirical truth table:

| posture | granted filesystem tool, pre-fix |
|---|---|
| green | **ran un-gated** |
| yellow | **ran un-gated** |
| red | pending (via posture, not the invariant) |

Mitigations that existed: red posture; the tool must first be granted. But the
documented guarantee ("the daemon refuses to bypass approval") was false for the
entire class of runtime-granted side-effecting tools.

## Decision

**Enforce the always-approval invariant at the dispatch choke point**, not only
in the (birth-time) resolver.

`ApprovalGateStep` now ORs a third elevation path alongside the per-tool
constraint and the genre policy:

```
policy_requires = side_effects in tool_policy.unconditional_approval_side_effects()
```

`unconditional_approval_side_effects()` is a new public helper in
`core/tool_policy` that derives the set (`{filesystem, external}`) **from the
unconditional rules themselves** — single source of truth, so a change to the
rules is honored by both the resolver and the gate with no second edit.

Rationale — **defense in depth at the choke point.** The gate is the
load-bearing safety surface; it must enforce the invariant regardless of how (or
whether) upstream resolution populated the constraint. Distributing the
invariant across every present and future resolution path (birth, grant, plugin,
marketplace, …) is fragile; enforcing it once, where the verdict is produced, is
not. The audit `gate_source` now records `side_effect_policy` when this path
fires, so an operator inspecting a ticket sees exactly which gate was
responsible.

## Consequences

**Positive.** The invariant is now true for *every* dispatch path. A
runtime-granted filesystem/external tool pends under all postures (verified live:
green/yellow/red → `pending_approval`, `gate_source=side_effect_policy`). The
docstrings of `browser_action`, `calendar_block`, and `isolate_process` —which
already claimed "external tools are always gated" — become accurate;
`misconception_log`'s claim is corrected to name the real mechanism.

**Behavior change.** Birth-baked side-effecting tools already gated, so they are
unaffected. Runtime-granted side-effecting tools now gate where they previously
ran — the intended correction. Two existing unit tests pinned the exact
`gate_source` string for filesystem tools and were updated to isolate their
intent (network side-effects, which the invariant deliberately does not cover).

**Scope / limit.** This is the *enforcement* fix. Grant-time resolution still
stores catalog-default constraints, so `preview` and the stored grant record may
show `requires_human_approval=False` for a side-effecting tool even though the
gate will pend it. A follow-up could also run `resolve_constraints` on the grant
path for preview/audit consistency; the security gap is closed regardless,
because the gate is the enforcement point.

## Alternatives considered

- **Fix only the grant-path resolution** (apply `resolve_constraints` in the
  grant lookup). Rejected as the *primary* fix: it closes one path but leaves the
  invariant dependent on every resolution path getting it right. Correct as an
  optional consistency follow-up, not as the safety guarantee.
- **Honest-docs only** (document that grants aren't gated). Rejected — that
  documents the gap instead of closing it, on the one invariant FSF treats as
  absolute.
- **Gate on all non-read-only side-effects (incl. network).** Rejected — it
  would over-gate read-class network tools (a researcher's `web_fetch`) that
  `tool_policy` deliberately leaves to genre policy. The invariant is scoped to
  the two side-effect classes the unconditional rules actually name.
