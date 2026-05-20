#!/usr/bin/env python3
"""Classify every file in soul_generated/ against live agents + audit
chain references. Identifies safe-to-delete candidates WITHOUT
deleting anything. Operator runs a second-step disposition after
reviewing the report.

Classification buckets per file:

  LIVE          — referenced by an agent row in the registry. Keep.
                  This is independent of agent status (active OR
                  archived); the registry row owns the file.
  CHAIN_ONLY    — not in registry, but its constitution_hash appears
                  in at least one audit chain event. Removing would
                  break chain verification for that historical event.
                  Keep.
  ORPHAN        — no registry row references the file path AND no
                  audit chain event references its constitution_hash.
                  Safe-to-delete candidate.
  PARSE_FAILED  — file couldn't be loaded as YAML or doesn't have a
                  constitution_hash field. Quarantine-style entry;
                  needs manual look.

Outputs:
  data/test-runs/orphan-constitution-audit-<ts>/
    report.md          — human-readable summary
    classifications.json — full per-file detail
    delete-candidates.txt — newline-list of file paths in the ORPHAN
                            bucket. Operator can review then pipe
                            through `xargs rm` after eyeballing.

Hippocratic gate (CLAUDE.md sec0):
  * Prove harm — 328 files for 40 agents = 288 candidates;
    accumulates GB over time; pollutes section-15 constitution-parse
    health if any parse-failed sneak in; no current operator process
    for triage.
  * Prove non-load-bearing for kernel — read-only audit script;
    writes only to data/test-runs/.
  * Prove alternative — manual file-by-file triage (rejected; 328
    files is unreasonable); blanket delete (rejected; would destroy
    chain-referenced or archived-agent records).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml  # type: ignore[import-not-found]

REPO_ROOT = Path(__file__).resolve().parents[1]
SOUL_DIR = REPO_ROOT / "soul_generated"
REGISTRY = REPO_ROOT / "data" / "registry.sqlite"
CHAIN = REPO_ROOT / "examples" / "audit_chain.jsonl"

# A constitution_hash is sha256 hex = 64 lowercase hex chars.
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")


def load_registry_paths_and_hashes() -> tuple[set[str], set[str]]:
    """Return (paths-referenced-by-any-agent, hashes-on-any-agent).

    Both queried unfiltered by status — an archived agent's record is
    still the canonical owner of its constitution file, until the
    operator explicitly retires the file alongside the agent.
    """
    conn = sqlite3.connect(REGISTRY)
    paths = set()
    hashes = set()
    for row in conn.execute(
        "SELECT constitution_path, constitution_hash FROM agents"
    ):
        path, h = row
        if path:
            # Normalize to absolute under REPO_ROOT for comparison.
            if not path.startswith("/"):
                paths.add(str((REPO_ROOT / path).resolve()))
            else:
                paths.add(str(Path(path).resolve()))
        if h:
            hashes.add(h)
    conn.close()
    return paths, hashes


def scan_chain_for_hashes() -> set[str]:
    """Return every sha256-shaped hex string mentioned anywhere in the
    audit chain. This is a SUPERSET — includes entry_hash, prev_hash,
    DNA fragments, plus any constitution_hash references inside
    event_data. We want a superset so we don't accidentally orphan
    a chain-referenced file.

    Reading the chain line-by-line keeps the working set small even
    for a 12MB file (~20k entries).
    """
    if not CHAIN.exists():
        print(f"WARN: chain not found at {CHAIN}", file=sys.stderr)
        return set()
    seen = set()
    with CHAIN.open() as f:
        for line in f:
            seen.update(HASH_RE.findall(line))
    return seen


def load_file_constitution_hash(path: Path) -> str | None:
    """Read the constitution_hash field from a YAML file. Returns None
    if parse fails or field missing."""
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "constitution_hash" in data:
            return str(data["constitution_hash"])
    except yaml.YAMLError:
        return None
    except OSError:
        return None
    return None


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "data" / "test-runs" / f"orphan-constitution-audit-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading registry from {REGISTRY}...")
    reg_paths, reg_hashes = load_registry_paths_and_hashes()
    print(f"  {len(reg_paths)} constitution paths in agents table")
    print(f"  {len(reg_hashes)} distinct constitution_hash values on agents")

    print(f"\nScanning chain at {CHAIN} for hex hashes...")
    chain_hashes = scan_chain_for_hashes()
    print(f"  {len(chain_hashes)} distinct sha256-shaped strings in chain")
    # Combine: registry hashes already include agent constitution_hash;
    # union with chain refs gives our "any reference exists" set.
    referenced_hashes = chain_hashes | reg_hashes

    files = sorted(SOUL_DIR.glob("*.constitution.yaml"))
    print(f"\nClassifying {len(files)} constitution files...")

    classifications: dict[str, list[dict]] = {
        "LIVE": [],
        "CHAIN_ONLY": [],
        "ORPHAN": [],
        "PARSE_FAILED": [],
    }
    for path in files:
        path_abs = str(path.resolve())
        h = load_file_constitution_hash(path)
        rec = {
            "path": str(path.relative_to(REPO_ROOT)),
            "size_bytes": path.stat().st_size,
            "constitution_hash": h,
            "in_registry_path": path_abs in reg_paths,
            "hash_referenced": (h in referenced_hashes) if h else False,
        }
        if h is None:
            classifications["PARSE_FAILED"].append(rec)
        elif rec["in_registry_path"]:
            classifications["LIVE"].append(rec)
        elif rec["hash_referenced"]:
            classifications["CHAIN_ONLY"].append(rec)
        else:
            classifications["ORPHAN"].append(rec)

    # Summary.
    total = sum(len(v) for v in classifications.values())
    print(f"\nClassification distribution:")
    for bucket, lst in classifications.items():
        print(f"  {bucket:14} {len(lst):4} files")
    assert total == len(files)

    # Write outputs.
    (out_dir / "classifications.json").write_text(json.dumps(classifications, indent=2))

    delete_candidates = [r["path"] for r in classifications["ORPHAN"]]
    (out_dir / "delete-candidates.txt").write_text("\n".join(delete_candidates) + "\n" if delete_candidates else "")

    # Markdown report.
    md = []
    md.append(f"# Orphan-constitution audit — {timestamp}")
    md.append("")
    md.append(f"- soul_generated files scanned: **{len(files)}**")
    md.append(f"- agents table constitution paths: {len(reg_paths)}")
    md.append(f"- chain sha256-shaped strings: {len(chain_hashes)}")
    md.append("")
    md.append("## Distribution")
    md.append("")
    md.append("| Bucket | Count | Disposition |")
    md.append("|---|---:|---|")
    md.append(f"| LIVE | {len(classifications['LIVE'])} | Keep — referenced by agent row. |")
    md.append(f"| CHAIN_ONLY | {len(classifications['CHAIN_ONLY'])} | Keep — `constitution_hash` referenced in audit chain. |")
    md.append(f"| ORPHAN | {len(classifications['ORPHAN'])} | **Safe-to-delete candidate.** No registry path. No chain hash ref. |")
    md.append(f"| PARSE_FAILED | {len(classifications['PARSE_FAILED'])} | Manual triage — YAML parse failed or no `constitution_hash` field. |")
    md.append("")
    md.append("## Bytes recovered if ORPHAN bucket deleted")
    md.append("")
    orphan_bytes = sum(r["size_bytes"] for r in classifications["ORPHAN"])
    md.append(f"- total orphan bytes: **{orphan_bytes:,} bytes** ({orphan_bytes / 1024 / 1024:.1f} MB)")
    md.append("")
    md.append("## PARSE_FAILED files (need manual look)")
    md.append("")
    if classifications["PARSE_FAILED"]:
        for r in classifications["PARSE_FAILED"][:20]:
            md.append(f"- `{r['path']}` ({r['size_bytes']} bytes)")
        if len(classifications["PARSE_FAILED"]) > 20:
            md.append(f"- ... +{len(classifications['PARSE_FAILED']) - 20} more (see classifications.json)")
    else:
        md.append("- (none)")
    md.append("")
    md.append("## ORPHAN sample (first 20)")
    md.append("")
    if classifications["ORPHAN"]:
        for r in classifications["ORPHAN"][:20]:
            md.append(f"- `{r['path']}`")
        if len(classifications["ORPHAN"]) > 20:
            md.append(f"- ... +{len(classifications['ORPHAN']) - 20} more (see delete-candidates.txt)")
    else:
        md.append("- (none)")
    md.append("")
    md.append("## How to delete (operator step — NOT done by this script)")
    md.append("")
    md.append("Review `delete-candidates.txt` then, if satisfied:")
    md.append("")
    md.append("```bash")
    md.append("# from repo root, after careful review:")
    md.append(f"xargs rm < {out_dir.relative_to(REPO_ROOT)}/delete-candidates.txt")
    md.append("```")
    md.append("")
    md.append("Then re-run `dev-tools/diagnostic/diagnostic-all.command` to")
    md.append("confirm section-05 constitution-parse health stays green.")
    md.append("")
    md.append("## What this audit guarantees")
    md.append("")
    md.append("1. No file referenced by any agent row (active or archived) is")
    md.append("   listed as ORPHAN. The `agents` table is the source-of-truth.")
    md.append("2. No file whose `constitution_hash` appears anywhere in the")
    md.append("   audit chain is listed as ORPHAN. Chain integrity preserved.")
    md.append("3. Files that fail to parse fall into PARSE_FAILED — never")
    md.append("   into ORPHAN. They need eyes, not automated deletion.")

    (out_dir / "report.md").write_text("\n".join(md))
    print(f"\nWrote report to {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
