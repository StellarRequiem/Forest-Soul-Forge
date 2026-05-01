# SW-track triune — operator runbook

**ADR:** [ADR-0034](../decisions/ADR-0034-software-engineering-track.md)
**Status:** Accepted (Phases A.1–A.6 + B.1 shipped 2026-04-30)
**Smoke tests:** `live-test-sw-coding-tools.command`,
`live-test-sw-coding-triune.command`,
`live-triune-file-adr-0034.command`

The SW-track triune is three coding-specialist agents bonded under a
named triune (Atlas + Forge + Sentinel). Each role claims an existing
genre rather than introducing a new one.

## The three roles

| Role | Genre | Side-effects ceiling | Job |
|---|---|---|---|
| **system_architect** (Atlas) | researcher | network | reads code, files ADRs, doesn't touch source |
| **software_engineer** (Forge) | actuator | external | writes code; filesystem + shell side-effects gated |
| **code_reviewer** (Sentinel) | guardian | read_only | second opinion / refusal arbiter on Forge's output |

## Why a triune

The operator wants engineering work the foundry can do on itself.
A single agent doing all three jobs collapses the verifier loop into
self-review. A triune of three peer-bonded agents — each in a different
genre with different ceilings — preserves the separation of concerns:

- Atlas designs (read-only research, files ADRs)
- Forge implements (write-and-test, gated by genre)
- Sentinel reviews (guardian-genre refusal arbiter)

Together they form a **verified-action loop** before any privileged
dispatch hits the operator's filesystem or shell.

## Bond a triune

Birth all three first via `/birth` (or the Forge UI), then bond:

```bash
fsf triune bond --name aurora \
  --instances <atlas_instance_id> <forge_instance_id> <sentinel_instance_id>
```

The `--no-restrict` flag opts out of the safety default; without it,
`delegate.v1` refuses out-of-bond calls. Use the default unless the
operator deliberately wants a porous triune.

The bond patches each agent's constitution YAML with the bond name
and emits one `triune.bonded` ceremony event in the audit chain.

## Tools each role gets

Per the SW-track kits in `config/tool_catalog.yaml::archetypes`:

| Tool | Atlas | Forge | Sentinel |
|---|:---:|:---:|:---:|
| `llm_think.v1` | ✓ | ✓ | ✓ |
| `memory_write.v1` | ✓ | ✓ | ✓ |
| `memory_recall.v1` | ✓ | ✓ | ✓ |
| `delegate.v1` | ✓ | ✓ | ✓ |
| `code_read.v1` | ✓ | ✓ | ✓ |
| `code_edit.v1` | | ✓ | |
| `shell_exec.v1` | | ✓ | |

`code_edit` and `shell_exec` are filesystem + external side-effects
respectively. Forge can fire them; Atlas (researcher / network ceiling)
and Sentinel (guardian / read_only) can't — the genre kit-tier check
refuses at runtime even if a constraint would otherwise allow it.

## Constraint flags worth knowing

In each agent's constitution YAML, the SW-track tools have:

- `code_read.v1` — `allowed_paths` constraint. The tool refuses any
  read outside the operator-approved root list. `code_read.v1` won't
  even touch `/etc/passwd` if `/etc` isn't allowlisted.
- `code_edit.v1` — same `allowed_paths` plus `requires_human_approval`
  by default for the actuator genre. Operator approves each write via
  the Approvals tab.
- `shell_exec.v1` — `allowed_commands` allowlist (argv[0] only) plus
  `requires_human_approval`. The runtime won't run `rm` if `rm` isn't
  on the list, and even allowed commands gate on operator approval.

## Drive a coding task end-to-end

The `live-test-sw-coding-triune.command` script is the canonical
demo. It:

1. Births the three SW-track agents with default kits
2. Bonds them under a fresh triune name
3. Has Atlas read a target file via `code_read.v1`
4. Has Atlas delegate to Forge via `delegate.v1`
5. Has Forge propose an edit via `llm_think.v1`
6. Has Forge stage the edit via `code_edit.v1` (gated)
7. Operator approves the staged edit
8. Has Forge run tests via `shell_exec.v1` (gated, `pytest` only)
9. Has Forge delegate to Sentinel for review
10. Sentinel emits a verified-or-rejected verdict via `llm_think.v1`

Every step appends to the audit chain. The full chain (typically
~21 audit events for a clean run) is visible via `/audit/tail` or
the frontend Audit tab.

## The meta-demo

`live-triune-file-adr-0034.command` is the meta-demonstration:
the SW-track triune itself files the ADR that defines them. The
operator runs the script; agents propose, debate, and write
ADR-0034 to disk. The audit chain proves the foundry can do
software work on its own definition.

## Known limits / by-design caveats

- **Bond is permanent at v0.1.** No `unbond` endpoint yet. Re-bond
  by archiving + re-birthing the three agents.
- **`shell_exec` allowlist is per-agent.** Operators set it in the
  constitution YAML at birth time. There's no global allowlist
  operators can opt into.
- **No automatic cross-triune handoff.** A triune talks among its
  three sisters by default. Cross-triune work goes through the
  operator OR a Y4 cross-domain bridge in a conversation room.

## Where to dig deeper

- **ADR**: `docs/decisions/ADR-0034-software-engineering-track.md`
- **Tools**: `src/forest_soul_forge/tools/builtin/code_read.py`,
  `code_edit.py`, `shell_exec.py`
- **Triune endpoint**: `daemon/routers/triune.py`
- **CLI**: `cli/triune.py` (`fsf triune bond`)
- **Smoke**: the three `live-test-sw-*.command` scripts
