#!/usr/bin/env python3
"""Chronological canon — the single machine-generated source of truth for FSF's
live metrics, plus a drift gate that re-measures disk and fails on mismatch.

WHY THIS EXISTS
---------------
A long-lived STATE.md accretes historical snapshots. Each was true at its burst.
But when they sit in the same visual register with no machine-legible fence, a
reader — human OR AI — grabs a three-week-old number and quotes it as present.
That exact failure happened: an external reviewer pulled `59,602 LoC / 2,800
tests / schema v20 / "tamper-PROOF"` from a buried 2026-05-13 stratum of STATE.md
and reported it as current, contradicting the (correct) README. The reviewer's
own meta-point — "this repo tells too many versions of its own truth" — was
proven by the reviewer tripping over it.

THE MECHANISM: canon / chronicle / gate.
  * CANON     — the present. Machine-generated from disk, written between
                <!-- CANON:BEGIN --> / <!-- CANON:END --> fences in STATE.md and
                mirrored to state_canon.json. Never hand-typed, so it cannot drift.
  * CHRONICLE — the past. Preserved for audit value but LOUDLY fenced as
                SUPERSEDED with an as-of stamp, so it can never be read as present.
  * GATE      — `--check` re-measures disk, diffs against the committed canon,
                exits non-zero on REPO drift. Runs in CI / the daily diagnostic
                harness so the canon is self-healing: drift -> red -> re-emit.

This is the `firewall`/`grounded` discipline (claims vs ground-truth) turned
reflexively on FSF's own documentation, and the "one machine-generated state
report — no hand-maintained counts" the external review prescribed.

THE FOUR HONESTY TIERS (operator protocol: no belief without verification).
  * repo       — CONTENT facts: derived only from file content, commit-agnostic,
                 reproducible from any clean checkout (LoC, ADRs, tools,
                 tests-as-STRUCTURAL-count, version, latest tag). HARD-GATED:
                 `--check` exits 1 if any disagrees with disk. This is exactly the
                 drift class the external review flagged.
  * provenance — VCS context: HEAD sha + commit count. Advances with every commit
                 (and a PR merge ref differs from the branch HEAD), so gating it
                 would cry wolf on every PR. Shown for "as-of which commit", never
                 gated.
  * runtime    — VOLATILE / host-local: registry agent counts (DB is gitignored,
                 absent in CI) and audit-chain length (grows continuously at
                 runtime). Shown, never gated — a CI checkout legitimately lacks
                 them and the live host legitimately moves them.
  * declared   — operator-asserted, not static-measurable (schema version: registry
                 PRAGMA user_version is 0; /healthz is the live check). Surfaced as
                 declared, never as measured, never gated.
  * dynamic    — requires execution (suite GREEN / pass count). Canon records the
                 STRUCTURAL test count and defers pass-count to a CI artifact. It
                 does NOT assert "N passing".

Usage:
  python3 dev-tools/state_canon.py --emit     # measure disk -> write json + STATE.md canon block
  python3 dev-tools/state_canon.py --check     # measure disk -> diff vs json -> exit 1 on REPO drift
  python3 dev-tools/state_canon.py --print     # measure disk -> print json, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANON_JSON = ROOT / "dev-tools" / "state_canon.json"
STATE_MD = ROOT / "STATE.md"
README_MD = ROOT / "README.md"
BEGIN = "<!-- CANON:BEGIN"   # prefix; full line carries a do-not-edit note
END = "<!-- CANON:END -->"

# Schema version is not derivable from static disk (registry PRAGMA user_version
# is 0; there is no migrations/ dir or SCHEMA_VERSION constant). Operator-declared,
# confirmed live via the daemon /healthz. One place, surfaced honestly as `declared`.
DECLARED_SCHEMA_VERSION = "v23"

# README "By the numbers" rows the gate enforces against disk. The README is the
# public claim surface — these core counts MUST match disk (hard-gated). Each regex
# pulls the bolded number from its row; a mismatch is a hard failure, a row that no
# longer matches yields a WARNING (the README was reworded — update the pattern).
README_CHECKS = {
    "python_loc":    r"Source LoC \(Python\)\*\*\s*\|\s*\*\*([\d,]+)\*\*",
    "test_files":    r"across \*\*([\d,]+) test files\*\*",
    "adr_files":     r"ADRs filed\*\*\s*\|\s*\*\*([\d,]+)\*\* files",
    "adr_unique":    r"\*\*([\d,]+)\*\* unique numbers",
    "builtin_tools": r"Built-in tools registered\*\*\s*\|\s*\*\*([\d,]+)\*\*",
}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def _wc_l(path: Path) -> int:
    """Count newlines, matching `wc -l` semantics exactly."""
    try:
        with path.open("rb") as fh:
            return fh.read().count(b"\n")
    except Exception:
        return 0


def measure() -> dict:
    """Every value comes from disk/git/registry at call time. No memory, no
    hand-typed constants except the explicitly-`declared` schema version."""
    src = ROOT / "src" / "forest_soul_forge"
    py = [p for p in src.rglob("*.py") if "__pycache__" not in p.parts]
    python_loc = sum(_wc_l(p) for p in py)

    test_files = [p for p in (ROOT / "tests").rglob("*.py")
                  if "__pycache__" not in p.parts and p.name.startswith("test_")]
    test_fn = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)
    test_functions = sum(len(test_fn.findall(p.read_text(errors="ignore")))
                         for p in test_files)

    adr = sorted((ROOT / "docs" / "decisions").glob("ADR-*.md"))
    # "unique ADR identifiers" = distinct stem after "ADR-" up to the next "-"/"." —
    # this matches the project's own count: it dedups amendments (ADR-0021 + ADR-0021-am
    # collapse to "0021") and counts the non-numeric placeholders (ADR-003X / ADR-003Y)
    # as the docs do. A strict \d{4} regex undercounts by silently dropping X/Y.
    adr_unique = {m.group(1) for p in adr if (m := re.match(r"ADR-([^-.]+)", p.name))}

    builtin = [p for p in (src / "tools" / "builtin").glob("*.py")
               if p.name != "__init__.py"]

    pyproject = (ROOT / "pyproject.toml").read_text()
    pyver = (m.group(1) if (m := re.search(
        r'^\s*version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)) else "?")

    # --- runtime (volatile / host-local; NOT reproducible from a checkout) ---
    agents_active = agents_archived = agents_total = None
    reg = ROOT / "data" / "registry.sqlite"          # gitignored — absent in CI
    if reg.exists():
        try:
            con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True, timeout=5)
            rows = dict(con.execute(
                "SELECT status, count(*) FROM agents GROUP BY status").fetchall())
            con.close()
            agents_active, agents_archived = rows.get("active", 0), rows.get("archived", 0)
            agents_total = sum(rows.values())
        except Exception:
            pass
    audit = ROOT / "examples" / "audit_chain.jsonl"  # tracked but grows at runtime
    audit_entries = _wc_l(audit) if audit.exists() else None

    return {
        "repo": {   # CONTENT facts — commit-agnostic, reproducible — HARD-GATED
            "python_loc": python_loc,
            "adr_files": len(adr),
            "adr_unique": len(adr_unique),
            "test_files": len(test_files),
            "test_functions": test_functions,
            "builtin_tools": len(builtin),
            "pyproject_version": pyver,
            "latest_tag": _git("for-each-ref", "--sort=-creatordate", "--count=1",
                               "--format=%(refname:short)", "refs/tags"),
        },
        "provenance": {   # VCS context — advances every commit; informational, not gated
            "head_sha": _git("rev-parse", "--short", "HEAD"),
            "commits_main": int(_git("rev-list", "--count", "HEAD") or 0),
        },
        "runtime": {   # volatile / host-local — informational, never gated
            "agents_active": agents_active,
            "agents_archived": agents_archived,
            "agents_total": agents_total,
            "audit_chain_entries": audit_entries,
        },
        "declared": {"schema_version": DECLARED_SCHEMA_VERSION},
        "dynamic": {"suite_status": "structural count only — pass/GREEN is a CI artifact, not asserted here"},
    }


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else ("n/a" if n is None else str(n))


def render_block(m: dict, stamp: str) -> str:
    r, pv, rt = m["repo"], m["provenance"], m["runtime"]
    return "\n".join([
        f"{BEGIN} — generated by dev-tools/state_canon.py --emit on {stamp}. "
        f"Do not hand-edit; run `--check` to verify against disk. -->",
        "",
        f"| Surface | Canon (generated {stamp}) |",
        "|---:|:---|",
        f"| Python LoC (`src/forest_soul_forge/`) | **{_fmt(r['python_loc'])}** |",
        f"| ADRs filed | **{_fmt(r['adr_files'])}** files / **{_fmt(r['adr_unique'])}** unique numbers |",
        f"| Builtin tools | **{_fmt(r['builtin_tools'])}** |",
        f"| Tests | **{_fmt(r['test_functions'])}** `def test_` across **{_fmt(r['test_files'])}** files "
        "_(structural count — suite GREEN/pass-count is a CI artifact, not asserted here)_ |",
        f"| Latest tag / pyproject version | **{r['latest_tag']}** / **{r['pyproject_version']}** |",
        f"| Schema version | **{m['declared']['schema_version']}** "
        "_(operator-declared; not static-measurable — daemon `/healthz` is the live check)_ |",
        "| _— below: informational, not gated —_ | |",
        f"| As of | **`{pv['head_sha']}`** · **{_fmt(pv['commits_main'])}** commits on `main` "
        "_(VCS provenance — advances every commit)_ |",
        f"| Registry agents | **{rt['agents_active']} active / {rt['agents_archived']} archived "
        f"/ {rt['agents_total']} total** _(local runtime DB — gitignored, absent in CI)_ |",
        f"| Audit chain entries | **{_fmt(rt['audit_chain_entries'])}** at `examples/audit_chain.jsonl` "
        "_(grows continuously at runtime — snapshot only)_ |",
        "",
        END,
    ])


def emit() -> int:
    m = measure()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    m["generated_at"] = stamp
    CANON_JSON.write_text(json.dumps(m, indent=2) + "\n")

    block = render_block(m, stamp)
    text = STATE_MD.read_text()
    if BEGIN in text and END in text:
        STATE_MD.write_text(text[: text.index(BEGIN)] + block + text[text.index(END) + len(END):])
        where = "STATE.md canon block replaced between fences"
    else:
        where = (f"NO FENCES in STATE.md — add a {BEGIN} ... --> / {END} pair, then re-run. "
                 "Block printed below:\n\n" + block)
    print(f"✅ emitted canon @ {stamp}\n   json  -> {CANON_JSON.relative_to(ROOT)}\n   state -> {where}")
    return 0


def check_readme(repo_live: dict) -> list:
    """Verify the README 'By the numbers' headline against disk. Returns rows of
    (key, claimed, disk, status) where status is 'ok' | 'drift' | 'unparsed'."""
    text = README_MD.read_text()
    rows = []
    for key, pat in README_CHECKS.items():
        m = re.search(pat, text)
        if not m:
            rows.append((key, None, repo_live[key], "unparsed"))
        else:
            claimed = int(m.group(1).replace(",", ""))
            rows.append((key, claimed, repo_live[key],
                         "ok" if claimed == repo_live[key] else "drift"))
    return rows


def check() -> int:
    if not CANON_JSON.exists():
        print(f"⛔ no canon at {CANON_JSON.relative_to(ROOT)} — run --emit first")
        return 1
    canon = json.loads(CANON_JSON.read_text())
    live = measure()
    repo_c, repo_l = canon.get("repo", {}), live["repo"]
    width = max(len(k) for k in (*repo_l, *live["runtime"], *live["provenance"]))

    print("GROUNDED — chronological-canon drift gate (disk vs committed canon)\n")
    print("  REPO content facts (hard-gated):")
    drift = []
    for k in repo_l:
        ok = repo_c.get(k) == repo_l.get(k)
        if not ok:
            drift.append(k)
        tail = "" if ok else f"   canon={repo_c.get(k)!r}  DISK={repo_l.get(k)!r}"
        print(f"    {'✅' if ok else '⛔'} {k:<{width}}  {repo_l.get(k)}{tail}")

    print("\n  README headline (hard-gated — public claim surface):")
    for key, claimed, disk, status in check_readme(repo_l):
        if status == "drift":
            drift.append(f"README:{key}")
            print(f"    ⛔ README:{key}  claim={claimed}  DISK={disk}")
        elif status == "unparsed":
            print(f"    ⚠️  README:{key}  row not found — update README_CHECKS regex")
        else:
            print(f"    ✅ README:{key}  {claimed}")

    print("\n  Informational (not gated — provenance + runtime):")
    for tier in ("provenance", "runtime"):
        for k in live[tier]:
            live_v, canon_v = live[tier].get(k), canon.get(tier, {}).get(k)
            if live_v is None:
                note = "n/a here (source absent — e.g. CI checkout)"
            elif live_v == canon_v:
                note = f"{live_v}"
            else:
                note = f"{live_v}  (canon snapshot was {canon_v} — moved since emit, expected)"
            print(f"    · {k:<{width}}  {note}")

    if drift:
        print(f"\n⛔ REPO DRIFT: {len(drift)} field(s) disagree with disk "
              f"({', '.join(drift)}). Run `python3 dev-tools/state_canon.py --emit` and commit.")
        return 1
    print(f"\n✅ canon matches disk on all {len(repo_l)} REPO content fields "
          f"(generated {canon.get('generated_at','?')}). Provenance + runtime informational.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FSF chronological canon: generate + gate.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit", action="store_true", help="measure disk -> write json + STATE.md canon")
    g.add_argument("--check", action="store_true", help="measure disk -> diff vs json -> exit 1 on REPO drift")
    g.add_argument("--print", dest="show", action="store_true", help="measure disk -> print only")
    a = ap.parse_args()
    if a.emit:
        return emit()
    if a.check:
        return check()
    print(json.dumps(measure(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
