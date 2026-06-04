#!/usr/bin/env python3
"""Forest's synaptic layer — a 30-second demo of trust that learns.

A small cognitive mesh routes a stream of problems across competing reasoning
nodes. Each attempt is *audited* (verified right or wrong), and that outcome —
nothing else — moves the trust weights. Watch the mesh:

  • learn WHO to trust for WHICH problem class (contextual),
  • ROUTE adaptively (Thompson sampling: explore the untested, exploit the proven),
  • QUARANTINE a node whose trust collapses (the safe, reversible direction),
  • prove WHY any trust value is what it is (provenance),
  • catch a forged track record (the ledger is hash-chained, tamper-evident).

Deterministic (seeded), pure stdlib, no cloud. Run:
    .venv/bin/python demo/synapse/synapse_demo.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from forest_soul_forge.synapse import TrustGraph  # noqa: E402

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _TTY else s
BOLD = lambda s: _c("1", s); DIM = lambda s: _c("2", s)
GREEN = lambda s: _c("1;32", s); RED = lambda s: _c("1;31", s)
CYAN = lambda s: _c("1;36", s); YEL = lambda s: _c("1;33", s)

def phase(emoji, title):
    print(); print(BOLD(f"{emoji}  {title}")); print(DIM("─" * 64))
def line(s=""): print(f"   {s}")


# Ground truth the demo can't see — each node's TRUE skill per problem class.
# The mesh must DISCOVER this from audited outcomes alone.
SKILL = {
    ("claude", "regulatory"): 0.85, ("gpt", "regulatory"): 0.55, ("qwen-local", "regulatory"): 0.45,
    ("claude", "arithmetic"): 0.70, ("gpt", "arithmetic"): 0.92, ("qwen-local", "arithmetic"): 0.80,
}
NODES = ["claude", "gpt", "qwen-local"]


def main() -> int:
    print(); print(BOLD(CYAN("  FOREST — the synaptic layer: trust that learns")))
    print(DIM("  Connections gain/lose weight from audited outcomes. Provenance-first."))
    rng = random.Random(20260604)
    g = TrustGraph()

    # ---- PHASE 1 — learn contextual trust from audited outcomes ----------
    phase("🧠", "EXPERIENCE — route problems, audit outcomes, update the weights")
    line("3 nodes compete on 2 problem classes. The mesh sees only pass/fail.")
    for _ in range(120):
        pc = rng.choice(["regulatory", "arithmetic"])
        node = g.best(NODES, pc, rng=rng)            # Thompson-route to a node
        success = rng.random() < SKILL[(node, pc)]   # audited outcome (ground truth)
        g.record(node, pc, success, evidence=f"task#{g._seq+1}")
    line()
    for pc in ("regulatory", "arithmetic"):
        line(BOLD(f"learned trust — {pc}:"))
        for n in NODES:
            s = g.trust(n, pc)
            lo, hi = s.interval()
            bar = "█" * int(s.mean * 20)
            line(f"   {n:<12} {s.mean:.2f} [{lo:.2f},{hi:.2f}] n={s.n:>3.0f}  {GREEN(bar)}")
        winner = max(NODES, key=lambda n: g.trust(n, pc).mean)
        truth = max(NODES, key=lambda n: SKILL[(n, pc)])
        ok = GREEN("✓ matches ground truth") if winner == truth else YEL("≠ ground truth (more data needed)")
        line(f"   → mesh trusts {BOLD(winner)} most for {pc}.  {ok}")

    # ---- PHASE 2 — adaptive routing ------------------------------------
    phase("🔀", "ROUTING — Thompson sampling exploits winners, still explores")
    counts = {n: 0 for n in NODES}
    for s in range(300):
        counts[g.best(NODES, "regulatory", rng=random.Random(s))] += 1
    line("next-100→300 routes for a 'regulatory' problem land:")
    for n in NODES:
        line(f"   {n:<12} {counts[n]:>3}   {DIM('(proven node favored; weak ones still sampled to stay honest)')}" if n=='claude' else f"   {n:<12} {counts[n]:>3}")

    # ---- PHASE 3 — a node rots → quarantine ----------------------------
    phase("⛔", "QUARANTINE — a node's trust collapses; the mesh isolates it")
    line(f"{RED('qwen-local')} starts failing 'regulatory' (drift / stale data)…")
    for _ in range(15):
        g.record("qwen-local", "regulatory", False, evidence="drift")
    q = g.quarantined(threshold=0.4, min_n=5)
    for s in q:
        line(f"   {RED('🚨 QUARANTINE CANDIDATE')}  {s.node}@{s.problem_class}  "
             f"trust={s.mean:.2f} (upper {s.interval()[1]:.2f} < 0.40, n={s.n:.0f})")
    line(f"   {DIM('Isolation is automatic + reversible. RELEASE is human-gated (ADR-0095).')}")

    # ---- PHASE 4 — provenance ------------------------------------------
    phase("📜", "WHY — every trust value traces to the outcomes that made it")
    prov = g.why("claude", "regulatory")
    wins = sum(1 for o in prov if o.success)
    line(f"claude@regulatory trust={g.trust('claude','regulatory').mean:.2f} is the fold of "
         f"{BOLD(str(len(prov)))} audited outcomes ({wins} ✓ / {len(prov)-wins} ✗).")
    line(f"   first: {DIM(prov[0].evidence)}   last: {DIM(prov[-1].evidence)}")

    # ---- PHASE 5 — tamper-evidence -------------------------------------
    phase("🔍", "INTEGRITY — the trust ledger is hash-chained; forgery is caught")
    ok, _ = g.verify()
    line(GREEN(f"✅ ledger verified — {g._seq+1} outcomes, chain intact"))
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trust.jsonl"; g.save(p)
        rows = p.read_text().splitlines()
        import json
        obj = json.loads(rows[10]); obj["success"] = not obj["success"]   # forge a win
        rows[10] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        p.write_text("\n".join(rows) + "\n")
        try:
            TrustGraph.load(p)
            line(RED("tamper NOT caught (unexpected)"))
        except ValueError as e:
            line(f"   forge one past outcome → reload → {RED('🚨 ' + str(e)[:60])}")
            line(f"   {GREEN('caught')} — you cannot rewrite a node's track record.")

    print(); print(BOLD("─" * 64))
    line("This is the synaptic layer of the cognitive mesh: machine experience as")
    line("an auditable, contextual, tamper-evident trust graph. Trust evolves; the")
    line("mesh routes, weakens, and quarantines on its own. Promotion stays human-gated.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
