"""``fsf install ...`` — promote staged forges into the live catalog.

Skill install (ADR-0031 T7) lands first because it doesn't require
the .fsf plugin loader (ADR-0019 T5) — a skill is just a manifest
file the catalog loader picks up.

Tool install lands in the next tranche (Round 2b); for now it's
documented as a manual file-copy step in the runbook.

Both subcommands emit a forge_*_installed audit chain entry so the
chain records who installed what, when, from which staged folder.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def run_skill(args: argparse.Namespace) -> int:
    """``fsf install skill <staged-dir>``."""
    from forest_soul_forge.cli._common import resolve_operator

    staged_dir = Path(args.staged_dir).resolve()
    if not staged_dir.exists():
        print(f"error: staged dir not found: {staged_dir}", file=sys.stderr)
        return 2
    manifest_path = staged_dir / "manifest.yaml"
    if not manifest_path.exists():
        print(
            f"error: no manifest.yaml in {staged_dir} — is this a "
            f"Skill Forge staged folder?",
            file=sys.stderr,
        )
        return 1

    # Parse to validate before copying — same loader the daemon uses.
    from forest_soul_forge.forge.skill_manifest import (
        ManifestError,
        parse_manifest,
    )
    try:
        skill = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except ManifestError as e:
        print(
            f"error: manifest validation failed: {e.path}: {e.detail}",
            file=sys.stderr,
        )
        return 1

    # Resolve install dir from settings (same as daemon).
    from forest_soul_forge.daemon.config import build_settings
    settings = build_settings()
    install_dir = Path(args.install_dir or settings.skill_install_dir).resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / f"{skill.name}.v{skill.version}.yaml"

    if target.exists() and not args.overwrite:
        print(
            f"error: {target} already exists. Pass --overwrite to replace.",
            file=sys.stderr,
        )
        return 1

    shutil.copyfile(manifest_path, target)
    print(f"[Skill install] copied manifest:\n  {manifest_path}\n  → {target}")

    # Emit audit-chain entry. We open the chain directly — single-
    # writer SQLite discipline is satisfied because the daemon is
    # paused for the duration of this CLI call by convention. ADR-
    # 0032 documents the direct-mode trade-off.
    from forest_soul_forge.core.audit_chain import AuditChain
    chain = AuditChain(settings.audit_chain_path)
    entry = chain.append(
        "forge_skill_installed",
        {
            "skill_name": skill.name,
            "skill_version": skill.version,
            "skill_hash": skill.skill_hash,
            "installed_from": str(staged_dir),
            "installed_to": str(target),
            "installed_by": resolve_operator(),
            "mode": "cli_direct",
        },
    )
    print(f"[Skill install] audit_seq={entry.seq} forge_skill_installed")

    # Try to call the daemon's reload endpoint — best-effort.
    if not args.no_reload:
        _try_reload(settings)
    return 0


def _try_reload(settings) -> None:
    """Best-effort POST /skills/reload. If the daemon isn't running
    we just print a hint — the next daemon boot will pick up the
    new manifest from disk."""
    import os
    import urllib.request
    import urllib.error
    base = (
        os.environ.get("FSF_DAEMON_URL")
        or "http://127.0.0.1:7423"
    )
    token = os.environ.get("FSF_API_TOKEN") or ""
    req = urllib.request.Request(
        base.rstrip("/") + "/skills/reload",
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"X-FSF-Token": token} if token else {}),
        },
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                import json
                body = json.loads(resp.read().decode("utf-8") or "{}")
                count = body.get("count", "?")
                print(f"[Skill install] reloaded daemon catalog → {count} skill(s)")
                errs = body.get("errors") or []
                if errs:
                    print("  reload reported errors:")
                    for e in errs:
                        print(f"    - {e}")
                return
            print(
                f"[Skill install] reload request returned HTTP {resp.status}",
                file=sys.stderr,
            )
    except urllib.error.URLError as e:
        print(
            f"[Skill install] daemon not reachable ({e.reason}); "
            f"the new manifest will load on next daemon boot.",
            file=sys.stderr,
        )
    except Exception as e:
        print(
            f"[Skill install] reload failed: {type(e).__name__}: {e}; "
            f"the new manifest will load on next daemon boot.",
            file=sys.stderr,
        )
