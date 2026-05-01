# C-1 zombie tool dissection (per-tool §0 verdict)

**Date:** 2026-04-30
**Author:** Forest Soul Forge harness
**Source finding:** Comprehensive repo audit (2026-04-30), Finding C-1
**Operator directive:** "each zombie tool will have to be dissected to decide what to keep or is just trash, proceed as such with each tool"

## What "zombie tool" means

Six entries in `config/tool_catalog.yaml` declare a tool spec but have **no on-disk Python implementation**:

```
baseline_compare.v1
correlation_window.v1
dns_lookup.v1
flow_summary.v1
log_grep.v1
packet_query.v1
```

They're listed in archetype kits for `network_watcher`, `log_analyst`, and `anomaly_investigator`. When those agents are birthed, their constitution lists tools the dispatcher refuses with `unknown_tool`. Test failures: 37 unit cases + 2 integration tests blocked.

## Comprehensive reference scan (all 6)

| Reference site | Hits |
|---|---:|
| Skill manifests (`examples/skills/`) | **0** |
| Audit chain entries (`examples/audit_chain.jsonl`) | **0** |
| Functional source code (`src/`) | **0** (only docstring examples for `packet_query`) |
| Archetype kits (`tool_catalog.yaml::archetypes`) | yes — 3 kits |
| ADR-0018 (original tool-catalog ADR) | yes — listed as part of original spec |
| `docs/tool-risk-guide.md` | yes — operator-facing risk descriptors |

**Conclusion:** zero functional consumers exist. The tools are referenced only in declarations and prose. No skill, no live code, no past audit entry would break if any of them changed or went away. The archetype kits that include them are themselves not exercised by any shipped skill.

## §0 gate, applied to the bucket

**Step 1 — prove harm:** YES. Catalog declares tools the dispatcher can't resolve → 37 unit failures + 2 integration failures + every birth of the original 5 archetype roles produces a constitution with broken references. Documented harm.

**Step 2 — prove non-load-bearing:** YES per the scan above. No skill, audit, or live code path depends on them.

**Step 3 — prove alternative is strictly better:** Per-tool below. Some get implemented (real capability gap), some get substituted (existing tool does the job better), one gets deferred to Phase G with a migration note.

**Step 4 — record outcome:** This document.

---

## Per-tool verdicts

### `dns_lookup.v1` — VERDICT: **IMPLEMENT**

**What it was meant to do:** Forward or reverse DNS lookup for a hostname or IP. Network side-effect (one UDP request to the resolver).

**Substitutes in current catalog:** None. `web_fetch.v1` does HTTP, not DNS. No tool resolves names.

**Phase G plans (from tool-catalog expansion survey 2026-04-30):** `whois_lookup.v1` is planned for blue-team intel, but DNS resolution is a foundational primitive that's strictly different from whois.

**Implementation effort:** Trivial — Python stdlib `socket.gethostbyname` / `socket.gethostbyaddr`. ~80-120 LoC including validation, audit shape, error handling. No new deps.

**Why IMPLEMENT not SUBSTITUTE or REMOVE:** DNS resolution is a real ongoing need for any agent that does network observation, threat hunting, or web research. Removing it without a replacement leaves a capability hole. Implementing it is small enough that it doesn't bloat scope.

**§0 verification:** Adding code is additive. Strictly improves the catalog (no longer lies about a tool that exists in the spec). All current archetype kits referencing `dns_lookup.v1` remain functional.

**Action:** Write `src/forest_soul_forge/tools/builtin/dns_lookup.py`. Register in `register_builtins()`. Add unit test. Verify catalog is now consistent.

---

### `log_grep.v1` — VERDICT: **SUBSTITUTE** with `log_scan.v1`

**What it was meant to do:** Run a regex match against a configured log store within a time window. Returns matching lines (capped) with timestamps and source IDs.

**Substitute in current catalog:** **`log_scan.v1`** (already implemented at `src/forest_soul_forge/tools/builtin/log_scan.py`, 250 LoC). Reads the description: "regex/pattern scan over a file or directory." Functionally equivalent — slightly broader scope (operates on file paths rather than a "log store" abstraction, which the daemon doesn't actually have).

**Why SUBSTITUTE not IMPLEMENT:** `log_scan.v1` exists, is tested, ships in the kits for `log_lurker` (security_low). Its semantics cover everything `log_grep.v1` was specced to do.

**Why SUBSTITUTE not DEPRECATE:** The two affected roles (`log_analyst`, `anomaly_investigator`) need regex-against-logs capability. Replacing the kit reference is a clean upgrade that requires no behavior change.

**Action:** In `tool_catalog.yaml::archetypes`:
- `log_analyst.standard_tools`: `log_grep.v1` → `log_scan.v1`
- `anomaly_investigator.standard_tools`: `log_grep.v1` → `log_scan.v1`

Then remove `log_grep.v1` from `tool_catalog.yaml::tools`. Add an ADR-0018 amendment recording the substitution + rationale.

**§0 verification:** Substitution preserves capability + uses an already-tested implementation. Strictly improves on a phantom catalog entry. Affected role kits gain a real working tool.

---

### `flow_summary.v1` — VERDICT: **SUBSTITUTE** with `traffic_flow_local.v1`

**What it was meant to do:** Summarize netflow records by 5-tuple over a time window. Returns counts, byte totals, direction breakdown.

**Substitute in current catalog:** **`traffic_flow_local.v1`** (already implemented, 258 LoC, security_mid kit). Description: "parse local OS flow tables." Same conceptual surface — flow records, 5-tuple aggregation. Different name, same purpose at v0.1 scale.

**Why SUBSTITUTE:** `traffic_flow_local.v1` is a real implementation that does flow analysis. The Phase G survey doesn't list a separate "netflow ingester" because none of the operator's machines run a netflow collector. Local flow tables are the realistic data source.

**Action:** In `tool_catalog.yaml::archetypes`:
- `network_watcher.standard_tools`: `flow_summary.v1` → `traffic_flow_local.v1`
- `anomaly_investigator.standard_tools`: `flow_summary.v1` → `traffic_flow_local.v1`

Then remove `flow_summary.v1` from `tool_catalog.yaml::tools`. ADR-0018 amendment.

**§0 verification:** As above — substitution with a tested implementation. Roles gain access to a real flow tool that already ships.

---

### `baseline_compare.v1` — VERDICT: **SUBSTITUTE** with `behavioral_baseline.v1` + `anomaly_score.v1`

**What it was meant to do:** Compare a metric series in the current window against a historical baseline. Returns z-score and absolute delta per bucket.

**Substitute in current catalog:** Two existing tools cover the same surface together:
- **`behavioral_baseline.v1`** (296 LoC) — "summary stats over an event stream"
- **`anomaly_score.v1`** (312 LoC) — "score deviation from a baseline"

The original `baseline_compare.v1` was specced as a single tool that did both jobs. The current catalog has them as two complementary tools, which is actually cleaner — separation of concerns.

**Why SUBSTITUTE not IMPLEMENT:** Two existing tools already cover the capability. Adding a third would create a redundant tool with overlapping semantics, which makes operator UX worse, not better.

**Action:** In `tool_catalog.yaml::archetypes`:
- `anomaly_investigator.standard_tools`: replace `baseline_compare.v1` with `behavioral_baseline.v1` + `anomaly_score.v1`

Then remove `baseline_compare.v1` from catalog. ADR-0018 amendment.

**§0 verification:** Strict improvement — `anomaly_investigator` gains two tools that work better in combination than the proposed single tool would have.

---

### `correlation_window.v1` — VERDICT: **SUBSTITUTE** with `log_correlate.v1`

**What it was meant to do:** Correlate events across two or more sources within a sliding time window. Returns event-pair clusters where source A and source B activity overlap.

**Substitute in current catalog:** **`log_correlate.v1`** (already implemented, 222 LoC). Description: "cross-source join over normalized log streams." Identical conceptual surface — temporal correlation across sources, returns matched event pairs.

**Why SUBSTITUTE:** `log_correlate.v1` exists, is tested in `test_b2_tools.py`, ships in security_mid swarm kits. The naming is just different — the spec is the same.

**Action:** In `tool_catalog.yaml::archetypes`:
- `anomaly_investigator.standard_tools`: `correlation_window.v1` → `log_correlate.v1`

Then remove `correlation_window.v1` from catalog. ADR-0018 amendment.

**§0 verification:** Substitution with a tested implementation. anomaly_investigator gains parity with security_mid swarm capability.

---

### `packet_query.v1` — VERDICT: **REMOVE + DEFER replacement to Phase G**

**What it was meant to do:** Query the local pcap store for packets matching a BPF filter within a time window. Returns up to N packet records (header + first 256 bytes of payload).

**Substitutes in current catalog:** None that match the use case. `traffic_flow_local.v1` operates on flow tables (5-tuple aggregation), not raw packets. There is no current pcap-reading capability.

**Why not IMPLEMENT now:**
- Real implementation requires `libpcap` bindings (heavy native dep)
- BPF filter compilation requires careful sandboxing (security risk if operator-supplied filters are passed unchecked to libpcap)
- Pcap reads of a "configured pcap store" require a pcap-store abstraction that doesn't exist
- ~300+ LoC of code, plus dep, plus security review
- The Phase G survey explicitly lists **`tshark_pcap_query.v1`** as the planned implementation: a wrapper around Wireshark's `tshark` CLI that handles BPF compilation safely, doesn't need libpcap bindings, and shells out to a known-good binary.

**Why REMOVE not just keep zombie:**
- Zero current consumer (no skill, no audit entry)
- Catalog should be honest about what exists
- The two affected roles (`network_watcher`, `anomaly_investigator`) get other network/flow tools via the other substitutions and remain functional without packet-level inspection until Phase G
- Phase G ADR (when filed) can re-add `tshark_pcap_query.v1` with a clear "supersedes prior packet_query.v1 spec" note

**Action:** In `tool_catalog.yaml::archetypes`:
- `network_watcher.standard_tools`: drop `packet_query.v1` (no replacement at v0.2 — gains `traffic_flow_local.v1` from `flow_summary` substitution above)
- `anomaly_investigator.standard_tools`: drop `packet_query.v1` (gains broader v2 toolkit per other substitutions)

Then remove `packet_query.v1` from catalog. Update the two doc-string examples in `tool_catalog.py` and `daemon/schemas/agents.py` to use a different illustrative tool (e.g., `dns_lookup.v1` or `timestamp_window.v1`). ADR-0018 amendment + Phase G migration note.

**§0 verification:** Removal does not break anything (no consumer). The only "loss" is a phantom catalog entry. Phase G provides a real replacement under a different name with safer implementation.

---

## Summary table

| Tool | Verdict | Action |
|---|---|---|
| `dns_lookup.v1` | **IMPLEMENT** | Write `dns_lookup.py`, register, test |
| `log_grep.v1` | **SUBSTITUTE** with `log_scan.v1` | Update kits, remove from catalog |
| `flow_summary.v1` | **SUBSTITUTE** with `traffic_flow_local.v1` | Update kits, remove from catalog |
| `baseline_compare.v1` | **SUBSTITUTE** with `behavioral_baseline.v1`+`anomaly_score.v1` | Update kits, remove from catalog |
| `correlation_window.v1` | **SUBSTITUTE** with `log_correlate.v1` | Update kits, remove from catalog |
| `packet_query.v1` | **REMOVE** (defer to Phase G `tshark_pcap_query.v1`) | Remove from kits + catalog, update doc-string examples |

**Net effect on archetype roles:**

| Role | Before | After |
|---|---|---|
| `network_watcher` | packet_query / flow_summary / dns_lookup / timestamp_window | traffic_flow_local / dns_lookup (new) / timestamp_window |
| `log_analyst` | log_grep / log_aggregate / timestamp_window | log_scan / log_aggregate / timestamp_window |
| `anomaly_investigator` | packet_query / log_grep / baseline_compare / correlation_window / timestamp_window | traffic_flow_local / log_scan / behavioral_baseline / anomaly_score / log_correlate / timestamp_window |

All 5 original archetype roles remain functional. 4 of 6 zombie entries get retired into existing tested implementations (zero new code needed for those). 1 entry gets a real implementation. 1 entry gets removed with a Phase G migration note.

## Execution order

1. **dns_lookup.v1 IMPLEMENT** — write impl + register + unit test, verify
2. **Catalog updates** (4 substitutions + 1 removal of packet_query + remove dns_lookup from "zombie" list since it now has an impl)
3. **Doc-string example updates** (`tool_catalog.py` + `daemon/schemas/agents.py`)
4. **ADR-0018 amendment** documenting the substitutions
5. **Verify** — full pytest suite shows the 37+2 C-1 cases now resolve
6. **Commit** as logical units (impl + catalog + ADR)

Each step verified before the next. Each commit lands only after its verification passes.
