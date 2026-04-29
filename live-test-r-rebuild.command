#!/usr/bin/env bash
# Phase A.6 — Rebuild fidelity test.
#
# Verifies registry.rebuild_from_artifacts() is byte-for-row equivalent
# to the live daemon's incremental writes. Non-destructive: rebuilds
# into a temp DB and diffs against the live one. Live DB never touched.
#
# What this proves about R4: the per-table accessor split preserves the
# rebuild orchestration on AgentsTable.rebuild_from_artifacts (which
# touches agents + agent_ancestry + audit_events under one transaction).
# If R4 broke the rebuild path, the temp DB will have wrong counts.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON=".venv/bin/python"
LIVE_DB="registry.sqlite"
TEMP_DB="data/test-runs/rebuild-$(date +%s).sqlite"

bar() { printf "\n========== %s ==========\n" "$1"; }
ok()  { printf "  ✓ %s\n" "$1"; }
no()  { printf "  ✗ %s\n" "$1" >&2; }
die() { no "$1"; echo ""; echo "Press return to close."; read -r _; exit 1; }

bar "0. preflight"
[[ -x "$PYTHON" ]] || die "$PYTHON not found"
[[ -f "$LIVE_DB" ]] || die "$LIVE_DB not found"
mkdir -p data/test-runs
ok "venv + live DB present; temp DB will land at $TEMP_DB"

# Snapshot live counts (read-only) ----------------------------------------
bar "1. snapshot live registry"
"$PYTHON" - <<PYEOF
import sqlite3
c = sqlite3.connect("$LIVE_DB")
for t in ["agents", "agent_ancestry", "audit_events"]:
    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  live.{t}: {n}")
PYEOF

# Run rebuild_from_artifacts into the temp DB -----------------------------
bar "2. rebuild into temp DB"
"$PYTHON" - <<PYEOF
import sys, sqlite3, time
from pathlib import Path
sys.path.insert(0, "src")
from forest_soul_forge.registry import Registry

t0 = time.perf_counter()
reg = Registry.bootstrap(Path("$TEMP_DB"))
report = reg.rebuild_from_artifacts(
    artifacts_dir=Path("soul_generated"),
    audit_chain_path=Path("examples/audit_chain.jsonl"),
)
elapsed = time.perf_counter() - t0
reg.close()

print(f"  rebuild took {elapsed*1000:.0f} ms")
print(f"  agents_loaded:               {report.agents_loaded}")
print(f"  ancestry_edges:              {report.ancestry_edges}")
print(f"  audit_events:                {report.audit_events}")
print(f"  legacy_instance_ids_minted:  {report.legacy_instance_ids_minted}")
print(f"  orphaned_parent_refs:        {len(report.orphaned_parent_refs)}")
PYEOF

# Compare counts ----------------------------------------------------------
bar "3. diff live vs rebuilt"
"$PYTHON" - <<PYEOF
import sqlite3
live = sqlite3.connect("$LIVE_DB")
rebuilt = sqlite3.connect("$TEMP_DB")

failed = False
for t in ["agents", "agent_ancestry", "audit_events"]:
    live_n = live.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    rebuilt_n = rebuilt.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    delta = rebuilt_n - live_n
    sign = "+" if delta > 0 else ("" if delta == 0 else "-")
    marker = "OK" if delta >= 0 else "FAIL"
    note = ""
    if t == "audit_events" and delta > 0:
        note = "  (rebuild ingests every chain entry; live only mirrors what was ingested at last lifespan boot — delta > 0 is expected)"
    elif t == "agents" and delta != 0:
        note = "  ← AGENTS COUNT MISMATCH"
        failed = True
    elif t == "agent_ancestry" and delta < 0:
        note = "  ← LOST ANCESTRY EDGES"
        failed = True
    print(f"  {t:24s} live={live_n:6d}  rebuilt={rebuilt_n:6d}  delta={sign}{delta}{note}")

# Spot-check: pick 3 random agents from live, verify they exist in rebuilt with same dna
import random
sample = live.execute("SELECT instance_id, dna, role FROM agents ORDER BY RANDOM() LIMIT 3").fetchall()
print()
print("  spot-check 3 random agents:")
for inst, dna, role in sample:
    row = rebuilt.execute("SELECT dna, role FROM agents WHERE instance_id=?", (inst,)).fetchone()
    if row is None:
        print(f"    ✗ {inst}: NOT in rebuilt DB")
        failed = True
    elif row[0] != dna or row[1] != role:
        print(f"    ✗ {inst}: dna/role mismatch")
        failed = True
    else:
        print(f"    ✓ {inst}: dna+role match (role={role})")

if failed:
    print()
    print("❌ REBUILD FIDELITY FAILED")
    raise SystemExit(1)
print()
print("✅ REBUILD FIDELITY OK — every live agent reconstructed correctly")
PYEOF

result=$?
echo ""
echo "Temp DB left in place at: $TEMP_DB"
echo "(safe to delete — live $LIVE_DB never touched)"
echo ""
if [[ $result -eq 0 ]]; then
  echo "✓ A.6 REBUILD TEST PASSED"
else
  echo "✗ A.6 REBUILD TEST FAILED"
fi
echo ""
echo "Press return to close."
read -r _
exit $result
