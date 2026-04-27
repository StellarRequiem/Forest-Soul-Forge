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


def run_tool(args: argparse.Namespace) -> int:
    """``fsf install tool <staged-dir>``.

    Default mode (--plugin, post-ADR-0019 T5): copies the staged
    spec.yaml + tool.py to data/plugins/<name>.v<version>/, calls
    POST /tools/reload, no daemon restart needed.

    --builtin mode: legacy in-source path. Copies tool.py to
    src/forest_soul_forge/tools/builtin/<name>.py + appends catalog
    YAML. Daemon restart still required. Used during dev when you
    want the tool in the source tree.

    Both modes:
      1. Validate the staged folder has tool.py + spec.yaml.
      2. Refuse if REJECTED.md is present unless --force.
      3. Emit forge_tool_installed audit-chain entry.
    """
    from forest_soul_forge.cli._common import resolve_operator
    import yaml

    # Mode dispatch — --builtin overrides the default.
    if getattr(args, "builtin", False):
        return _run_tool_builtin_mode(args)
    return _run_tool_plugin_mode(args)


def _run_tool_plugin_mode(args: argparse.Namespace) -> int:
    """Plugin mode (default, post-ADR-0019 T5)."""
    from forest_soul_forge.cli._common import resolve_operator
    import yaml

    staged_dir = Path(args.staged_dir).resolve()
    if not staged_dir.exists():
        print(f"error: staged dir not found: {staged_dir}", file=sys.stderr)
        return 2
    spec_path = staged_dir / "spec.yaml"
    tool_path = staged_dir / "tool.py"
    rejected_path = staged_dir / "REJECTED.md"
    for p, label in [(spec_path, "spec.yaml"), (tool_path, "tool.py")]:
        if not p.exists():
            print(
                f"error: {label} missing in {staged_dir} — is this a "
                f"Tool Forge staged folder?",
                file=sys.stderr,
            )
            return 1
    if rejected_path.exists() and not args.force:
        print(
            f"error: {staged_dir} has REJECTED.md (Tool Forge static "
            f"analysis or generated tests failed). Pass --force to "
            f"install anyway.",
            file=sys.stderr,
        )
        return 1

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    name = spec.get("name") or ""
    version = spec.get("version") or "1"
    if not name:
        print("error: spec.yaml has no name field", file=sys.stderr)
        return 1

    # Resolve plugins_dir from settings.
    from forest_soul_forge.daemon.config import build_settings
    settings = build_settings()
    plugins_dir = (
        Path(args.plugins_dir)
        if args.plugins_dir
        else Path(settings.plugins_dir)
    )
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target_dir = plugins_dir / f"{name}.v{version}"
    if target_dir.exists() and not args.overwrite:
        print(
            f"error: {target_dir} already exists. Pass --overwrite to "
            f"replace.",
            file=sys.stderr,
        )
        return 1
    if target_dir.exists() and args.overwrite:
        shutil.rmtree(target_dir)
    target_dir.mkdir()

    # Copy spec + tool.py + tests if present.
    shutil.copyfile(spec_path, target_dir / "spec.yaml")
    shutil.copyfile(tool_path, target_dir / "tool.py")
    test_path = staged_dir / f"test_{name}.py"
    if test_path.exists():
        shutil.copyfile(test_path, target_dir / f"test_{name}.py")

    print(f"[Tool install] plugin staged at:\n  {target_dir}")

    # Audit entry.
    from forest_soul_forge.core.audit_chain import AuditChain
    chain = AuditChain(settings.audit_chain_path)
    entry = chain.append(
        "forge_tool_installed",
        {
            "tool_name": name,
            "tool_version": str(version),
            "side_effects": spec.get("side_effects"),
            "installed_from": str(staged_dir),
            "installed_to": str(target_dir),
            "installed_by": resolve_operator(),
            "mode": "cli_plugin",
            "force": bool(args.force),
        },
    )
    print(f"[Tool install] audit_seq={entry.seq} forge_tool_installed")

    if not args.no_reload:
        _try_tools_reload()
    else:
        print(
            "  --no-reload: the new tool will load on next daemon boot "
            "(or run `curl -X POST .../tools/reload`).",
            file=sys.stderr,
        )

    return 0


def _try_tools_reload() -> None:
    """Best-effort POST /tools/reload mirroring the skill version."""
    import os
    import urllib.request
    import urllib.error
    import json

    base = (os.environ.get("FSF_DAEMON_URL") or "http://127.0.0.1:7423")
    token = os.environ.get("FSF_API_TOKEN") or ""
    req = urllib.request.Request(
        base.rstrip("/") + "/tools/reload",
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
                body = json.loads(resp.read().decode("utf-8") or "{}")
                count = body.get("registered_count", "?")
                loaded = body.get("plugins_loaded", "?")
                print(
                    f"[Tool install] reloaded daemon → "
                    f"{count} tools registered ({loaded} plugin(s))"
                )
                errs = body.get("plugin_errors") or []
                if errs:
                    print("  reload reported plugin errors:")
                    for e in errs:
                        print(f"    - {e}")
                return
            print(
                f"[Tool install] reload returned HTTP {resp.status}",
                file=sys.stderr,
            )
    except urllib.error.URLError as e:
        print(
            f"[Tool install] daemon not reachable ({e.reason}); "
            f"the new tool will load on next daemon boot.",
            file=sys.stderr,
        )
    except Exception as e:
        print(
            f"[Tool install] reload failed: {type(e).__name__}: {e}; "
            f"the new tool will load on next daemon boot.",
            file=sys.stderr,
        )


def _run_tool_builtin_mode(args: argparse.Namespace) -> int:
    """Legacy in-source builtin path. Daemon restart required."""
    from forest_soul_forge.cli._common import resolve_operator
    import yaml

    staged_dir = Path(args.staged_dir).resolve()
    if not staged_dir.exists():
        print(f"error: staged dir not found: {staged_dir}", file=sys.stderr)
        return 2
    spec_path = staged_dir / "spec.yaml"
    tool_path = staged_dir / "tool.py"
    diff_path = staged_dir / "catalog-diff.yaml"
    rejected_path = staged_dir / "REJECTED.md"
    for p, label in [(spec_path, "spec.yaml"), (tool_path, "tool.py")]:
        if not p.exists():
            print(
                f"error: {label} missing in {staged_dir} — is this a "
                f"Tool Forge staged folder?",
                file=sys.stderr,
            )
            return 1
    if rejected_path.exists() and not args.force:
        print(
            f"error: {staged_dir} has REJECTED.md (Tool Forge static "
            f"analysis or generated tests failed). Pass --force to "
            f"install anyway.",
            file=sys.stderr,
        )
        return 1

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    name = spec.get("name") or ""
    version = spec.get("version") or "1"
    if not name:
        print(f"error: spec.yaml has no name field", file=sys.stderr)
        return 1

    # Install destinations.
    repo_root = _repo_root()
    builtin_dir = (
        Path(args.builtin_dir)
        if args.builtin_dir
        else repo_root / "src" / "forest_soul_forge" / "tools" / "builtin"
    )
    catalog_path = (
        Path(args.catalog_path)
        if args.catalog_path
        else repo_root / "config" / "tool_catalog.yaml"
    )
    builtin_dir.mkdir(parents=True, exist_ok=True)
    target_py = builtin_dir / f"{name}.py"
    if target_py.exists() and not args.overwrite:
        print(
            f"error: {target_py} already exists. Pass --overwrite to "
            f"replace.",
            file=sys.stderr,
        )
        return 1

    # Copy the .py.
    shutil.copyfile(tool_path, target_py)
    print(f"[Tool install] copied:\n  {tool_path}\n  → {target_py}")

    # Append the catalog entry. We DON'T do a structured YAML merge
    # (preserving comments + ordering matters); we append the diff
    # block as raw YAML at the end of the tools: section. The
    # operator can re-order later if they care.
    if diff_path.exists():
        appended = _append_catalog_entry(catalog_path, diff_path)
        if appended:
            print(f"[Tool install] catalog entry appended → {catalog_path}")
        else:
            print(
                f"[Tool install] catalog already contains {name}.v{version} — "
                f"skipped append.",
                file=sys.stderr,
            )
    else:
        print(
            f"[Tool install] no catalog-diff.yaml found; you'll need to "
            f"add the catalog entry by hand.",
            file=sys.stderr,
        )

    # Audit entry.
    from forest_soul_forge.daemon.config import build_settings
    from forest_soul_forge.core.audit_chain import AuditChain
    settings = build_settings()
    chain = AuditChain(settings.audit_chain_path)
    entry = chain.append(
        "forge_tool_installed",
        {
            "tool_name": name,
            "tool_version": str(version),
            "side_effects": spec.get("side_effects"),
            "installed_from": str(staged_dir),
            "installed_to": str(target_py),
            "installed_by": resolve_operator(),
            "mode": "cli_direct",
            "force": bool(args.force),
        },
    )
    print(f"[Tool install] audit_seq={entry.seq} forge_tool_installed")

    print()
    print(
        "Restart the daemon (or rebuild the container) to pick up the "
        "new tool. ADR-0019 T5 will lift the restart requirement once "
        "the .fsf plugin loader lands."
    )
    print(
        "Don't forget to register the tool class in "
        "src/forest_soul_forge/tools/builtin/__init__.py — "
        "`register_builtins(registry)` needs an explicit entry."
    )
    return 0


def _repo_root() -> Path:
    """Best-effort repo-root resolution. Walks up from this file
    looking for pyproject.toml. Falls back to cwd."""
    cur = Path(__file__).resolve()
    for _ in range(6):
        cur = cur.parent
        if (cur / "pyproject.toml").exists():
            return cur
    return Path.cwd()


def _append_catalog_entry(catalog_path: Path, diff_path: Path) -> bool:
    """Append the catalog-diff entry to the catalog file. Returns
    True if a new entry was appended; False if the entry's name was
    already present (idempotent re-install).

    Implementation detail: we parse the diff (always a single-item
    list) and the catalog YAML, check for duplicate by name+version,
    then re-emit the catalog. Comments inside the catalog are LOST
    on re-emit (PyYAML doesn't round-trip them) — operators who
    care should re-add by hand. T7 of ADR-0030 (catalog hot-reload)
    will do better.
    """
    import yaml
    diff_data = yaml.safe_load(diff_path.read_text(encoding="utf-8"))
    if not isinstance(diff_data, list) or not diff_data:
        return False
    new_entry = diff_data[0]
    new_key = (new_entry.get("name"), str(new_entry.get("version") or "1"))

    catalog_data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    tools_list = catalog_data.get("tools") or []
    for entry in tools_list:
        if (entry.get("name"), str(entry.get("version") or "1")) == new_key:
            return False
    tools_list.append(new_entry)
    catalog_data["tools"] = tools_list
    catalog_path.write_text(
        yaml.safe_dump(catalog_data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return True


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
