# ADR-0057 — Skill Forge UI (operator-direct)

**Status:** Accepted
**Date:** 2026-05-09
**Burst:** B201
**Deciders:** Alex Price (orchestrator)
**Related:** ADR-0030 (Tool Forge), ADR-0031 (Skill Forge), ADR-0044 (Kernel + SoulUX), ADR-0056 (Experimenter agent)

## Context

The Skill Forge engine (ADR-0031 T1) ships propose-only behavior in `forge/skill_forge.py`: an operator types `fsf forge skill "describe a workflow"`, the engine calls the configured LLM provider, parses the YAML reply against `SkillDef`, and stages the manifest under `data/forge/skills/staged/<name>.v<version>/`. The install path (ADR-0031 T7, `cli/install.py::run_skill`) takes a staged dir, copies the manifest into `data/forge/skills/installed/<name>.v<version>.yaml`, and emits a `forge_skill_installed` audit event.

Both halves are CLI-only. The SoulUX Skills tab is read-only — its current empty state literally tells the operator to "Forge one with `fsf forge skill '...'` and copy the manifest." That is acceptable for developer-operators but inconsistent with the SoulUX flagship's stated audience (ADR-0044 §positioning): non-developer operators who manage agents from a graphical surface.

The substrate is mostly there. What's missing is HTTP plumbing and a frontend wizard.

## Decision

Add an operator-direct skill creation path through the SoulUX UI. Concretely:

1. **POST `/skills/forge`** — accepts `{description, name?, version?}`, calls the existing `forge_skill_sync` against the daemon's configured LLM provider, returns `{staged_path, manifest, forge_log_excerpt}`. Wraps the existing engine; does not duplicate logic.
2. **POST `/skills/install`** — accepts `{staged_path, overwrite?}`, mirrors the logic of `cli/install.py::run_skill` (parse manifest, copy to installed dir, emit `forge_skill_installed`), returns `{installed_path, audit_seq, manifest}`.
3. **GET `/skills/staged`** — lists pending staged manifests (those forged but not yet installed). Read for the new "Forged proposals" UI subsection.
4. **DELETE `/skills/staged/{name}/{version}`** — discards a staged proposal without installing.
5. **Frontend** — a "New Skill" button on the Skills tab opens a modal with a description textarea + optional name/version. After Forge, the modal previews the staged manifest with an Install button or a Discard button.
6. **Approvals routing** — staged proposals appear as a separate **Forged proposals** subsection in the Approvals tab, distinct from tool-call approvals. Per Alex's directive (B201 scoping discussion 2026-05-09), the two are different governance shapes (one is per-call dispatch, the other is per-artifact admission) and conflating them harms legibility.

## Consequences

**Positive:**
- Closes the loop for non-developer operators per the ADR-0044 audience claim.
- Reuses the proven CLI engine; the HTTP layer is a thin wrapper, no duplicated propose-or-install logic.
- The audit chain shape is unchanged — same `forge_skill_proposed` and `forge_skill_installed` events fire whether the trigger is CLI or UI. An auditor reading the chain cannot tell the difference (deliberate; the artifact is what matters, not the click that produced it).
- Smith experimenter (ADR-0056) can also drive these endpoints. The same `/skills/forge` + `/skills/install` are reachable from his propose-cycle path; B202 follow-up gives Smith his own caller and uses the same approval surface.

**Negative / trade-offs:**
- The propose stage hits the LLM provider, so latency is multi-second. The frontend must show a spinner; the endpoint should not have an aggressive timeout.
- Failure modes from the propose stage (LLM returns invalid YAML, schema mismatch, provider down) need to surface meaningfully in the modal — not just a generic 500.
- The "Forged proposals" subsection on Approvals is *new UI surface*, not just a wired-up existing one. Adds frontend module count + needs its own polling cadence.

**Out of scope for this ADR (deferred):**
- Tool Forge UI — paired ADR-0058 covers it. Tools have an extra dimension (Python implementation, not just a YAML manifest) and that dimension's design choice (stub-only vs prompt-template-tool vs plugin-protocol) is settled separately.
- Inline manifest editing of an already-installed skill — out of scope; the install path is overwrite-or-create only. Editing live skills is governance-sensitive and gets its own ADR if it's wanted.
- Smith driving the new endpoints — natural follow-up but it's a runtime test, not a code burst. Tracked separately.

## Alternatives considered

**A. Punt to a CLI helper button** — show a "Copy CLI command" button on the Skills tab so the operator pastes it into their terminal. Rejected: doesn't actually close the loop for non-developer operators. The whole point of SoulUX is that you don't need to drop to CLI for routine artifact management.

**B. Inline shell exec from the daemon** — have the daemon shell out to `fsf forge skill` via subprocess. Rejected: shells out across the security boundary, complicates the threat model, no real win versus calling the engine directly.

**C. Unified Approvals tab with tool-call approvals + forged proposals** — initial sketch. Rejected by Alex 2026-05-09 because the two are different governance shapes and visual conflation reduces legibility.

## Tranches

| T | Scope | Burst |
|---|---|---|
| T1 | POST `/skills/forge` + POST `/skills/install` + auth + validation | B201 |
| T2 | GET `/skills/staged` + DELETE `/skills/staged/{name}/{version}` | B201 |
| T3 | Frontend: New Skill modal on Skills tab | B201 |
| T4 | Frontend: Forged proposals subsection on Approvals tab | B201 |
| T5 | Tests for all four endpoints | B201 |
| T6 | Smith driving the endpoints (runtime demo) | B203 |

## Verification

- Unit tests for both POST endpoints cover: happy path (description in → staged dir created → install lands in installed dir → audit event with correct shape), 401 without token, invalid manifest rejected at install, overwrite flag behavior.
- Live smoke: from the SoulUX Skills tab, type "summarize the last 10 audit chain entries", click Forge, click Install. Skills tab refresh shows the new skill in `0 → 1 installed`. Audit tab shows the matching `forge_skill_proposed` + `forge_skill_installed` pair.
