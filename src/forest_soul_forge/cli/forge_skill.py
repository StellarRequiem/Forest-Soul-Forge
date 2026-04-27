"""``fsf forge skill`` runner — ADR-0031 T1 propose-only.

Bridges the argparse Namespace to ``forge.skill_forge.forge_skill``.
Mirrors the structure of cli/forge_tool.py — same provider
construction (``cli/_common.build_provider``), same staged-folder
discipline, same forge.log convention.

Stops after the propose stage. Skill runtime (ADR-0031 T2) and install
path (T7) are separate tranches.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run(args: argparse.Namespace) -> int:
    from forest_soul_forge.cli._common import build_provider, resolve_operator
    from forest_soul_forge.forge.skill_forge import forge_skill_sync
    from forest_soul_forge.forge.skill_manifest import ManifestError

    description = (args.description or "").strip()
    if not description:
        print("error: empty description", file=sys.stderr)
        return 2

    provider = build_provider(args.provider)
    out_dir = Path(args.out_dir).resolve()

    print(
        f"[Skill Forge] proposing manifest via {provider.name}...",
        file=sys.stderr,
    )

    try:
        result = forge_skill_sync(
            description=description,
            provider=provider,
            out_dir=out_dir,
            forged_by=resolve_operator(),
            name_override=args.name,
            version=args.version,
        )
    except ManifestError as e:
        print(
            f"[Skill Forge] manifest validation failed: {e.path}: {e.detail}",
            file=sys.stderr,
        )
        print(
            "  The provider's YAML didn't validate. forge.log was not "
            "written; re-run with a clearer description, or pass "
            "--describe-tools to surface the available tool catalog "
            "(future flag).",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"[Skill Forge] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    skill = result.skill
    print()
    print(f"  name:           {skill.name}")
    print(f"  version:        {skill.version}")
    print(f"  description:    {skill.description.splitlines()[0]}")
    print(f"  requires_tools: {list(skill.requires)}")
    print(f"  steps:          {len(skill.steps)}")
    print(f"  skill_hash:     {skill.skill_hash}")
    print()
    print(f"[Skill Forge] staged at:\n  {result.staged_dir}")
    print(f"  manifest.yaml: {result.manifest_path}")
    print(f"  forge.log:     {result.log_path}")
    print()
    print("Next steps (ADR-0031 T2-T7 will automate these):")
    print("  1. Review manifest.yaml — sanity-check the requires list")
    print("     and the step ordering.")
    print("  2. For each tool in requires that doesn't exist yet, run")
    print("     `fsf forge tool '...'` to author the primitive.")
    print("  3. T2 will add a skill runtime that walks the manifest and")
    print("     dispatches each step through the existing tool runtime.")
    return 0
