"""``fsf verify`` — the operator's independent verification command.

The one thing the operator runs to check, *without trusting the daemon or the
agent driving it*, that the system hasn't been corrupted or tampered with — and
to see, in plain English straight from the tamper-evident log, what the agents
actually did.

Why this exists (ADR-0095 era / operator-extension reframe): the dashboard is one
*view* of the system; the operator drives the fleet through an agent and verifies
out-of-band. This command is that out-of-band check. It reads on-disk artifacts
directly — the append-only hash-linked audit chain, the registry SQLite, git — so
it needs no running daemon and trusts nothing the daemon says.

Checks:
  1. Audit chain — recompute the hash links; any forged/edited past event breaks it.
  2. Registry DB — ``PRAGMA integrity_check`` (the store that was just corrupted).
  3. Code — the git commit you're actually running.
  4. Recent activity — the last N audit events, summarized from the chain itself.

Exit 0 iff every integrity check passes, so it's scriptable / cron-able.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "verify",
        help="Independently verify system integrity + recent agent activity (no daemon needed).",
    )
    p.add_argument(
        "--chain", default=None,
        help="Audit chain JSONL path (default: the live chain if present, "
             "else the committed examples/audit_chain.sample.jsonl fixture).",
    )
    p.add_argument(
        "--db", default="data/registry.sqlite",
        help="Registry SQLite path (default: data/registry.sqlite).",
    )
    p.add_argument(
        "--recent", type=int, default=10,
        help="How many recent audit events to summarize (default: 10).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Machine-readable output instead of the human report.",
    )
    p.set_defaults(_run=run)


def _check_chain(path: str, recent_n: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Verify the audit chain hash-links and pull the last ``recent_n`` events.

    Reads the file directly via the canonical AuditChain verifier — a forged or
    edited past event breaks the hash linkage and is reported with its seq.
    """
    cp = Path(path)
    if not cp.exists():
        return {"ok": False, "reason": f"chain file not found: {path}",
                "entries": 0, "head_seq": None, "broken_at": None}, []
    try:
        from forest_soul_forge.core.audit_chain import AuditChain
    except Exception as e:  # pragma: no cover — package import guard
        return {"ok": False, "reason": f"could not import AuditChain: {e}",
                "entries": 0, "head_seq": None, "broken_at": None}, []
    chain = AuditChain(cp)
    r = chain.verify()
    head = chain.head
    info = {
        "ok": bool(r.ok),
        "reason": r.reason,
        "entries": getattr(r, "entries_verified", None),
        "head_seq": head.seq if head is not None else None,
        "broken_at": getattr(r, "broken_at_seq", None),
    }
    recent: list[dict[str, Any]] = []
    try:
        for e in chain.read_all()[-recent_n:]:
            ed = e.event_data or {}
            label = (ed.get("tool_name") or ed.get("ceremony_name")
                     or ed.get("agent_name") or ed.get("instance_id") or "")
            recent.append({
                "seq": e.seq,
                "ts": getattr(e, "timestamp", None),
                "event": e.event_type,
                "label": str(label)[:60],
            })
    except Exception:
        pass
    return info, recent


def _check_db(path: str) -> dict[str, Any]:
    """Read-only ``PRAGMA integrity_check`` — safe to run while the daemon holds
    the DB (WAL mode + ``mode=ro``); never writes, never locks out the writer."""
    dp = Path(path)
    if not dp.exists():
        return {"ok": False, "result": f"db not found: {path}"}
    try:
        conn = sqlite3.connect(f"file:{dp}?mode=ro", uri=True, timeout=5)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        return {"ok": result == "ok", "result": result}
    except Exception as e:
        return {"ok": False, "result": f"error: {e}"}


def _git_state() -> dict[str, Any]:
    def g(cmd: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", *cmd], capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None
    return {
        "head": g(["rev-parse", "--short", "HEAD"]),
        "branch": g(["rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(g(["status", "--porcelain"])),
    }


def run(args: argparse.Namespace) -> int:
    from forest_soul_forge.cli.chronicle import _resolve_chain_path
    chain_path = str(_resolve_chain_path(args.chain))
    chain, recent = _check_chain(chain_path, args.recent)
    db = _check_db(args.db)
    git = _git_state()
    all_ok = bool(chain["ok"]) and bool(db["ok"])

    if args.json:
        print(json.dumps(
            {"ok": all_ok, "chain": chain, "db": db, "git": git, "recent": recent},
            indent=2, default=str,
        ))
        return 0 if all_ok else 1

    def mark(ok: bool) -> str:
        return "OK  " if ok else "FAIL"

    print("FSF — independent verification")
    print("=" * 34)
    chain_line = (f"[{mark(chain['ok'])}] audit chain — {chain['entries']} events, "
                  f"head seq {chain['head_seq']}")
    if not chain["ok"]:
        chain_line += f"\n        ✗ {chain['reason']} (broken at seq {chain['broken_at']})"
    print(chain_line)
    print(f"[{mark(db['ok'])}] registry DB integrity — {db['result']}")
    dirty = " · uncommitted changes" if git["dirty"] else ""
    print(f"[ -- ] code @ git {git['head']} ({git['branch']}){dirty}")

    print(f"\nRecent agent activity (last {len(recent)}, from the tamper-evident log):")
    if not recent:
        print("  (none)")
    for r in recent:
        print(f"  #{str(r['seq']):<6} {r['event']:<26} {r['label']}")

    print()
    print("VERIFIED — integrity intact" if all_ok
          else "INTEGRITY FAILURE — investigate before trusting state")
    return 0 if all_ok else 1
