# ADR-0030 — Tool Forge

- **Status:** Proposed
- **Date:** 2026-04-27
- **Supersedes:** Subsumes the "Skill Forge" sketch in [docs/notes/skill-and-tool-trees.md](../notes/skill-and-tool-trees.md). That note conflated tool authoring (this ADR) with skill authoring (ADR-0031); the two are distinct layers and get separate ADRs.
- **Related:** ADR-0018 (tool catalog — Tool Forge produces entries here), ADR-0019 (tool execution runtime — Forged tools must satisfy the Tool Protocol), ADR-0021 (genres — Forged tools inherit side-effects classification rules), ADR-0024 (horizons — Tool Forge is the H1 "wow" feature), ADR-0031 (Skill Forge — composes tools that this ADR creates).

## Context

After T1–T6 of ADR-0019, the runtime can dispatch tools with full audit + constraint + genre enforcement. The catalog has 8 tools. **Adding a 9th currently requires:**

1. Hand-write `src/forest_soul_forge/tools/builtin/<name>.py` implementing the Tool Protocol.
2. Hand-edit `config/tool_catalog.yaml` to add the descriptor (name, version, side_effects, archetype_tags, input_schema, description).
3. Hand-write `tests/unit/test_<name>.py`.
4. Restart the daemon.

That's correct for the project's operators (who *are* the developers), but it's the wrong friction for two upcoming audiences:

- **External operators** who want to add a domain-specific tool (`scan_my_pcap_directory.v1`, `query_my_grafana.v1`) without learning the codebase architecture.
- **Skill Forge** (ADR-0031), which decomposes English-language workflow descriptions into tool-call DAGs — and routinely needs a primitive that doesn't exist yet, so it asks Tool Forge to create one.

The Tool Forge is the missing ramp from "I can describe what a tool should do in English" to "the runtime can dispatch a hash-pinned, constraint-governed, audit-trailed implementation of that tool."

## Decision

The Tool Forge is a **6-stage pipeline** triggered by either a CLI command or a future frontend tab. Each stage produces an artifact the next stage consumes. The pipeline is interruptible — operators stop at any point if the output looks wrong.

```
  ┌────────────────────────────────────────────────────────────────┐
  │ 1. DESCRIBE                                                    │
  │    Operator types plain English: "I need a tool that takes a   │
  │    CIDR range and returns the count of distinct source IPs     │
  │    seen in the last hour from packet captures."                │
  └────────────────────────────────────────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ 2. PROPOSE  —  LLM emits a candidate ToolSpec                  │
  │    name, version, side_effects, archetype_tags, input_schema,  │
  │    output_schema, description. Operator reviews + edits.        │
  └────────────────────────────────────────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ 3. CODEGEN  —  LLM emits a Python module satisfying the Tool   │
  │    Protocol. Static-analysis pass (ruff + bandit-style)         │
  │    flags risk patterns. Test scaffold generated alongside.      │
  └────────────────────────────────────────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ 4. REVIEW  —  Operator reads the diff. Sees the proposed       │
  │    side_effects classification, the static-analysis flags, the  │
  │    generated tests. Edits or rejects.                           │
  └────────────────────────────────────────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ 5. PROVE   —  Sandbox-run the generated tests. Operator sees    │
  │    pass/fail before the tool can be installed.                  │
  └────────────────────────────────────────────────────────────────┘
                                ▼
  ┌────────────────────────────────────────────────────────────────┐
  │ 6. INSTALL —  Emit .py to data/forge/staged/, append catalog    │
  │    diff, audit-chain entry. T5 plugin loader picks it up on     │
  │    next daemon boot (or hot-reload, when T5 lands).              │
  └────────────────────────────────────────────────────────────────┘
```

### ToolSpec — the manifest the LLM emits at stage 2

```yaml
name: distinct_source_ips_in_cidr
version: "1"
description: |
  Count distinct source IPs from a packet capture that fall within a
  given CIDR range, restricted to the last hour.
side_effects: read_only      # filesystem? network? external? — LLM picks, operator confirms
archetype_tags: [network_watcher, anomaly_investigator]
input_schema:
  type: object
  required: [cidr]
  properties:
    cidr:
      type: string
      pattern: '^\d+\.\d+\.\d+\.\d+/\d+$'
      description: "CIDR range, e.g. '10.0.0.0/24'."
    pcap_path:
      type: string
      description: "Path to a .pcap file. Defaults to the active capture."
output_schema:
  type: object
  properties:
    count: {type: integer, minimum: 0}
    cidr: {type: string}
    window_seconds: {type: integer}
risk_flags: []               # populated by stage 3 static analysis
forged_at: "2026-04-27T18:00:00Z"
forged_by: "alex"
forge_provider: "frontier:claude-3-7-opus"  # or local:llama3
forge_prompt_digest: "sha256:..."           # hash of the description for reproducibility
```

The ToolSpec is the **content-addressed identity** of the forged tool. Re-forging from the same English description with the same provider should produce the same digest. Tool Forge stores spec + Python alongside in `data/forge/staged/<name>.v<version>/`.

### Codegen contract (stage 3)

The LLM is prompted with:
1. The Tool Protocol contract from `src/forest_soul_forge/tools/base.py` (verbatim).
2. The ToolSpec from stage 2.
3. A short reference example (the timestamp_window source).
4. Style rules: pure-function preferred, async required, no global state, no dynamic imports, no `os.system`/`subprocess` unless side_effects ≥ filesystem.

Output is a single Python file with:
- A module docstring matching the ToolSpec description.
- A class implementing `Tool` (name, version, side_effects, validate, execute).
- A test module written alongside, exercising at minimum: happy path, invalid args, edge case from the description.

### Static-analysis pass (stage 3 → 4)

Hard checks (block install on failure):
- Module imports parse cleanly.
- The Tool Protocol contract is satisfied (`isinstance(t, Tool)` passes).
- `side_effects` value is in `SIDE_EFFECTS_VALUES`.
- No `eval`, `exec`, `compile`, `__import__("os").system`, or other obvious sandbox escapes.

Soft flags (surface to operator for review, don't block):
- Network calls when `side_effects == "read_only"` (probably mis-classified).
- File writes when `side_effects in {"read_only", "network"}` (probably mis-classified).
- `subprocess` usage at any tier (potentially fine, always worth a second look).
- LLM call (`provider.complete`) without `tokens_used` plumbed through (cost won't show on character sheet).
- `# TODO` or `# FIXME` markers (probably an LLM hedge).

Static analysis runs `ruff` for syntax + style and a bespoke checker for the soft-flag rules. We don't pull in `bandit` directly — its flagging logic is broader than the project's threat model, and tuning it is more work than the bespoke pass.

### Audit-chain entries

Tool Forge emits two new event types. Both go through `AuditChain.append()`:

```
forge_tool_proposed   { name, version, description, side_effects,
                        forged_by, forge_provider, forge_prompt_digest,
                        risk_flags }
forge_tool_installed  { name, version, content_hash,  -- of the .py
                        proposed_seq,                  -- backref to proposed entry
                        forged_by, installed_at }
```

A rejected forge (operator backs out at stage 4 or stage 5 fails) emits no `installed` entry — the proposed entry stands alone, marking that an operator considered creating this tool and decided not to. That's intentional: the chain is also a record of **what wasn't built**.

### Storage layout

```
data/forge/
  staged/
    <name>.v<version>/
      spec.yaml         # the ToolSpec
      tool.py           # the generated implementation
      test_<name>.py    # the generated tests
      forge.log         # human-readable record of the conversation +
                        # static-analysis output
  installed/
    <name>.v<version>.fsf  # plugin package (post-T5)
```

Staged tools aren't loaded by the daemon. Installation packages them as a `.fsf` plugin (per ADR-0019 T5 — landing soon) and moves them to `installed/`. Until T5 ships, "install" is "copy `tool.py` to `src/forest_soul_forge/tools/builtin/` and append the catalog YAML manually" — Tool Forge generates the diff but the operator applies it.

### CLI surface (v0.1)

```bash
$ fsf forge tool "describe what the tool should do"
[Tool Forge] proposing ToolSpec via local:llama3...
  name: distinct_source_ips_in_cidr
  version: 1
  side_effects: read_only
  archetype_tags: [network_watcher, anomaly_investigator]
  input_schema: <CIDR + pcap_path>
  output_schema: <count + cidr + window_seconds>

Continue? [y/N/edit] y

[Tool Forge] generating Python implementation...
[Tool Forge] static-analysis: 0 hard, 0 soft flags
[Tool Forge] running generated tests in sandbox...
  test_happy_path .................... PASS
  test_invalid_cidr .................. PASS
  test_empty_capture ................. PASS

Review:  data/forge/staged/distinct_source_ips_in_cidr.v1/
Install? [y/N] y

[Tool Forge] installed → data/forge/installed/distinct_source_ips_in_cidr.v1.fsf
[Tool Forge] catalog diff written → data/forge/staged/.../catalog-diff.yaml
[Tool Forge] audit_seq=243 forge_tool_installed
```

CLI v0.2 will add `--provider`, `--no-test` (skip prove stage for trusted providers), `--from-spec spec.yaml` (skip propose stage when the operator already wrote the spec), and `--dry-run`.

### Frontend surface (deferred)

A "Forge" tab on the frontend wraps the same pipeline: text input → live spec preview → live diff preview → static-analysis report → run-tests button → install button. Same audit events, same storage. Frontend depends on `.fsf` plugin loader (T5) for the install step.

## Implementation tranches

- **T1** — CLI scaffold. `fsf forge tool` command parses args, calls the active provider with the codegen prompts, writes outputs to `data/forge/staged/`. **No installation yet** — CLI emits the diff, operator applies manually. Audit events for proposed only.
- **T2** — Static-analysis pass. Hard + soft flags wired. Operator review prompt enforces non-empty hard-flag handling.
- **T3** — Test sandbox. Generated tests run in an isolated venv (or container; pick at T3 design time); pass/fail surfaced before install.
- **T4** — Install path. Catalog YAML diff applied, audit `forge_tool_installed` event emitted. Until ADR-0019 T5 lands, the file goes to `tools/builtin/` and the daemon needs a restart.
- **T5** — `.fsf` packaging. Once ADR-0019 T5 ships, install emits `.fsf` to `installed/`, daemon hot-reloads.
- **T6** — Frontend "Forge" tab.
- **T7** — `--from-spec`, `--provider`, `--dry-run`, and other CLI ergonomics.
- **T8** — Re-forge / refresh path. Operator updates the description; Tool Forge regenerates and produces a versioned diff (v1 → v2). Old version stays installed; agents can pin specific versions.

## Trade-offs and rejected alternatives

**LLM picks side_effects vs. operator declares.** LLM picks (operator confirms). The LLM has the description in front of it and can ask "does this read or write?" more reliably than asking the operator to pre-classify. The static-analysis pass at stage 3 catches misclassifications.

**Bespoke flag checker vs. bandit.** Bespoke. Bandit's defaults are tuned for general Python code; we want flags grounded in our threat model (`provider.complete` cost plumbing, sandbox escape patterns, tier-vs-call mismatch). Bespoke is ~200 lines once.

**Block install on soft flags vs. surface and continue.** Surface and continue. Soft flags are signal, not policy. Operator can override a soft flag with a one-line justification that gets hashed into the audit chain. Hard flags block.

**Single Python file vs. multi-file packages.** Single file for v1. Most tools fit in one file; multi-file is a complication that earns its place once a forged tool actually needs it.

**Reproducibility (forge_prompt_digest).** Two operators forging from the same description with the same provider should arrive at the same tool. They won't — LLMs are stochastic. The `forge_prompt_digest` is for *traceability* (you can recover what was asked) not *reproducibility* (you can't rerun and get the same code). We tell the operator that explicitly.

**What if the LLM emits malicious code?** That's the static-analysis pass's job to catch. It won't catch everything. Therefore: **install is operator-confirmed**, the audit chain records who installed what, and the .fsf plugin loader (T5) sandboxes execution. Defense in depth, not just the codegen.

**Provider economics.** Codegen against a frontier provider produces better Python than a small local model. ADR-0026 covers the cost model; Tool Forge defaults to the active provider (operator's choice) and surfaces token usage in the audit entry.

## Consequences

**Positive.**
- Adding a tool drops from "navigate the codebase + edit YAML + write tests" to "describe what you want."
- Skill Forge (ADR-0031) gets a backend that can fill primitive gaps autonomously.
- Audit chain captures the forge provenance — a tool's lineage includes the English that birthed it.
- New operators productive faster.

**Negative.**
- Codegen quality is a function of the provider. Local-only operators get a worse experience (mitigated by `--from-spec` for hand-written specs).
- Soft-flag noise risks alarm fatigue. Tighten the rules over time.
- Tools forged from the same description across versions of the codegen prompt diverge. Treat the prompt as part of the contract.

**Neutral.**
- Storage grows with rejected forges (every `staged/` entry persists). Operator-cleanup CLI command in T7.

## Cross-references

- ADR-0018 — tool catalog (Forge writes here)
- ADR-0019 — tool execution runtime (Forge produces tools that satisfy this contract)
- ADR-0024 — horizons (Tool Forge is the H1 "wow" feature)
- ADR-0026 — provider economics (codegen cost model)
- ADR-0031 — Skill Forge (the consumer that drives much of this ADR's design)
- docs/notes/skill-and-tool-trees.md — original prior-art note
