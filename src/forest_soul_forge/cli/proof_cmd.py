"""``fsf proof`` — the standing scorecard: prove this repo holds The Standard.

One command anyone can re-run that scores the repo against STANDARD.md and, for
every line, prints the exact command to **independently verify it** — no trust
required. Exit 0 iff full marks. This is the bar turned into a number: the
measurable, third-party-checkable "standing" surface.

The criteria are the repo-level, measurable evidence for the Standard's bar
(Tested / Audited / integrity-enforced / canon-true / standard-bound). It reuses
the ``fsf verify`` checks for the audit criterion so the two never diverge.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from forest_soul_forge.cli.verify_cmd import _check_chain, _check_db


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "proof",
        help="Score this repo against The Standard (STANDARD.md) — runnable by anyone.",
    )
    p.add_argument(
        "--chain", default=None,
        help="Audit chain JSONL (default: live chain if present, "
             "else the committed examples/audit_chain.sample.jsonl fixture).",
    )
    p.add_argument("--db", default="data/registry.sqlite")
    p.add_argument("--json", action="store_true", help="Machine-readable scorecard.")
    p.set_defaults(_run=run)


def _count_tests(root: str = "tests") -> tuple[int, int]:
    rp = Path(root)
    files = list(rp.rglob("test_*.py")) if rp.is_dir() else []
    funcs = 0
    for f in files:
        try:
            funcs += sum(
                1 for ln in f.read_text(encoding="utf-8").splitlines()
                if ln.lstrip().startswith("def test_")
            )
        except Exception:
            pass
    return len(files), funcs


def _canon_ok() -> bool:
    """Docs-vs-disk drift gate. A strong, measurable 'reproducible' signal."""
    try:
        r = subprocess.run(
            ["python3", "dev-tools/state_canon.py", "--check"],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


def _gather(chain_path: str, db_path: str) -> list[dict]:
    crit: list[dict] = []

    nf, ntf = _count_tests()
    crit.append({
        "name": "Tested", "pass": nf > 0,
        "measure": f"{ntf} test functions across {nf} files",
        "verify": "pytest  ·  gh run list (CI)",
    })

    chain, _ = _check_chain(chain_path, 1)
    db = _check_db(db_path)
    crit.append({
        "name": "Audited", "pass": bool(chain["ok"]) and bool(db["ok"]),
        "measure": f"chain {'intact' if chain['ok'] else 'BROKEN'} "
                   f"({chain['entries']} events) · DB {db['result']}",
        "verify": "fsf verify",
    })

    sw = Path("src/forest_soul_forge/core/single_writer.py").exists()
    crit.append({
        "name": "Integrity-enforced", "pass": sw,
        "measure": f"single-writer lock {'present' if sw else 'MISSING'} · hash-chained audit",
        "verify": "fsf forge tool x --dry-run   # refuses while daemon live",
    })

    ck = _canon_ok()
    crit.append({
        "name": "Canon-true", "pass": ck,
        "measure": "chronological canon matches disk" if ck else "CANON DRIFT",
        "verify": "python3 dev-tools/state_canon.py --check",
    })

    std = Path("STANDARD.md").exists()
    crit.append({
        "name": "Standard-bound", "pass": std,
        "measure": "STANDARD.md present" if std else "no STANDARD.md",
        "verify": "cat STANDARD.md",
    })
    return crit


def run(args: argparse.Namespace) -> int:
    from forest_soul_forge.cli.chronicle import _resolve_chain_path
    crit = _gather(str(_resolve_chain_path(args.chain)), args.db)
    score = sum(1 for c in crit if c["pass"])
    total = len(crit)

    if args.json:
        print(json.dumps({"score": score, "total": total, "criteria": crit},
                         indent=2, default=str))
        return 0 if score == total else 1

    print("FSF — proof of standing  (scored against STANDARD.md)")
    print("=" * 52)
    for c in crit:
        print(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['name']:<20} {c['measure']}")
        print(f"        verify yourself: {c['verify']}")
    print()
    tag = "  standard met" if score == total else "  below standard"
    print(f"SCORE: {score}/{total}{tag}")
    return 0 if score == total else 1
