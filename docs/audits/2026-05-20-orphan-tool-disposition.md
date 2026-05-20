# Orphan tool disposition — 2026-05-20 (post-B436)

**Driver:** B437 — loose-ends sweep
**Operator:** Alex Price
**HEAD at investigation:** aee1e2a (B436 — first signed commit)

## What the harness reported

`diagnostic-15-wiring-cross-check` consistently flagged:

> **[FAIL]** tool wiring coverage (3 orphan tools) —
> `operator_profile_write.v1`; `personal_recall.v1`; `security_scan.v1`
> — in catalog, zero archetypes/agents carry them.

These three tools had substrate (source under
`src/forest_soul_forge/tools/builtin/`) and catalog entries
(`config/tool_catalog.yaml`), but no archetype kit, genre default,
constitution-template allowed_tools entry, or live agent
constitution referenced them. They were truly unreachable.

## Per-tool analysis

### `personal_recall.v1` — ADR-0076 T4

Hybrid retrieval (BM25 + cosine RRF) over the operator's
PersonalIndex. Catalog declares it `genre-gated to {companion,
assistant, operator_steward, domain_orchestrator}`. Of those four
roles, only `assistant` had a kit defined in `archetypes.*`.

**Disposition:** wire to `archetypes.assistant.standard_tools`.
B437 lands the kit entry.

The other three roles (`companion`, `operator_steward`,
`domain_orchestrator`) appear in `trait_tree.yaml` but lack
archetype kits in the catalog. When those kits land in a future
burst, `personal_recall.v1` should be appended there too — the
gate list in the tool's description is the canonical home set.

### `security_scan.v1` — ADR-0062

Supply-chain IoC scanner. Reads `config/security_iocs.yaml`
(v2 catalog with 21 rules covering Shai-Hulud / MCP-STDIO-RCE /
LiteLLM / Axios / Grafana / MoneyForward / TeamPCP incidents).
`side_effects=read_only`. Catalog description says "Any agent in
any genre can run it."

Direct sibling pattern to `git_local_scan.v1` (B432), which we
wired to `wiring_sentinel` in this session's P3. Both are
read-only IoC-catalog-consuming scanners designed for the
WiringSentinel scheduled-task cadence.

**Disposition:** wire to
`archetypes.wiring_sentinel.standard_tools`. B437 lands it.
Same archetype, same scheduling cadence, same genre ceiling.

### `operator_profile_write.v1` — ADR-0068 T2

Operator profile mutator. `requires_human_approval=True`. Takes a
dotted field_path + new_value + reason; atomically updates
`data/operator/profile.yaml` and emits the
`operator_profile_changed` audit event with before/after diff.

The natural home is whatever role holds operator-truth write
responsibility — operator companion or similar. Per-call
approval gating provides the safety regardless of kit placement,
but the role-fit decision is non-trivial:

- The live operator_companion agent
  (`operator_companion_40ceaf894e87`) is alive but lacks an
  archetype kit entry in the catalog. Its tools come from
  constitution-direct grants, not kit composition.
- The `assistant` archetype kit is the most permissive candidate
  but conflates the operator-companion (writes operator-truth)
  with the assistant (general operator-facing helper).
- Adding write capability without first deciding the kit shape
  for `operator_companion` would constrain future ADR-0068 T4-T6
  work.

**Disposition: DEFERRED.** Leaving `operator_profile_write.v1`
orphan in the catalog until the operator decides which archetype
should own operator-truth write. Section-15 will continue to
flag this as FAIL — that's the correct signal until disposition.

## Post-B437 expected state

After B437 lands:

| Tool | Carrier | Section-15 |
|---|---|---|
| `personal_recall.v1` | `assistant` kit | resolved → INFO (in kit but no alive agent yet, since no `assistant`-role agent is born) |
| `security_scan.v1` | `wiring_sentinel` kit | resolved → INFO (in kit, no alive WiringSentinel-D5 yet) |
| `operator_profile_write.v1` | none (deferred) | continues to FAIL |

Section-15 orphan count narrows 3 → 1 (the deferred one).

## What would change this disposition

- ADR-0068 T4-T6 lands `operator_companion` archetype kit. At that
  point, append `operator_profile_write.v1` to the new kit; this
  audit doc is closed.
- A future role (different from operator_companion) takes
  operator-truth write responsibility — same outcome, different
  kit name. The operator decides.
- The tool itself is retired from catalog (unlikely — substrate
  code is sound and the operator-truth surface is real). In that
  case section-15 stops flagging it because it's no longer
  cataloged.

## Cross-references

- `docs/decisions/ADR-0068-personal-context-store.md` —
  Personal Context Store / OperatorProfile substrate
- `docs/decisions/ADR-0076-vector-index.md` — PersonalIndex
  retrieval contract that `personal_recall.v1` implements
- `docs/decisions/ADR-0062-supply-chain-scanner.md` —
  IoC catalog + scanner contract that `security_scan.v1`
  implements
- `docs/decisions/ADR-0081-substrate-wiring-coverage.md` —
  section-15 mechanic that flags orphan tools
- `docs/decisions/ADR-0084-github-push-pipeline-posture.md` —
  Tier 1 hardening this loose-ends sweep follows up on
