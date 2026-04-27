"""``fsf forge tool`` runner — ADR-0030 T1.

Bridges the argparse Namespace to ``forge.tool_forge.forge_tool``.
Builds a provider directly from ``DaemonSettings`` so the CLI works
without a running daemon — a forge is a one-shot operation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def run(args: argparse.Namespace) -> int:
    """Entry point for ``fsf forge tool ...``."""
    description = (args.description or "").strip()
    if not description:
        print("error: empty description", file=sys.stderr)
        return 2

    provider = _build_provider(args.provider)
    out_dir = Path(args.out_dir).resolve()

    print(f"[Tool Forge] proposing ToolSpec via {provider.name}...",
          file=sys.stderr)

    from forest_soul_forge.forge.tool_forge import (
        ForgeError, SpecParseError, forge_tool_sync,
    )

    try:
        result = forge_tool_sync(
            description=description,
            provider=provider,
            out_dir=out_dir,
            forged_by=_resolve_operator(),
            name_override=args.name,
            version=args.version,
            proposed_only=args.dry_run,
        )
    except SpecParseError as e:
        print(f"[Tool Forge] propose stage failed: {e}", file=sys.stderr)
        print(
            "  The provider didn't emit a valid ToolSpec. Check "
            "data/forge/staged/.../forge.log if a partial run wrote one.",
            file=sys.stderr,
        )
        return 1
    except ForgeError as e:
        print(f"[Tool Forge] {e}", file=sys.stderr)
        return 1

    spec = result.spec
    print()
    print(f"  name:           {spec.name}")
    print(f"  version:        {spec.version}")
    print(f"  side_effects:   {spec.side_effects}")
    print(f"  archetype_tags: {list(spec.archetype_tags)}")
    print(f"  description:    {spec.description.splitlines()[0]}")
    print()

    if result.proposed_only:
        print(f"[Tool Forge] propose stage only (--dry-run). "
              f"Spec written to:\n  {result.spec_path}", file=sys.stderr)
        print(f"[Tool Forge] forge log:\n  {result.log_path}", file=sys.stderr)
        return 0

    if not args.no_prompt:
        ans = input("Continue to codegen? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("[Tool Forge] aborted at propose review.", file=sys.stderr)
            print(f"  spec preserved at: {result.spec_path}", file=sys.stderr)
            return 0

    # If we got here, codegen ran during forge_tool_sync (proposed_only
    # was False). Surface the static-analysis report (ADR-0030 T2)
    # before the staging summary so hard flags are the first thing the
    # operator sees.
    analysis = result.analysis
    if analysis is not None and analysis.flags:
        print()
        print(f"[Tool Forge] static analysis: "
              f"{len(analysis.hard_flags)} hard, "
              f"{len(analysis.soft_flags)} soft")
        for f in analysis.hard_flags:
            tag = f" L{f.line}" if f.line else ""
            print(f"  [HARD] {f.rule}{tag}: {f.message}")
        for f in analysis.soft_flags:
            tag = f" L{f.line}" if f.line else ""
            print(f"  [soft] {f.rule}{tag}: {f.message}")
        print()
    elif analysis is not None:
        print()
        print("[Tool Forge] static analysis: clean (0 hard, 0 soft)")

    if result.staging_blocked and not args.force:
        print(
            f"[Tool Forge] REJECTED — hard flags fired. Folder kept at:\n"
            f"  {result.staged_dir}\n"
            f"  REJECTED.md lists the hard flags.\n\n"
            f"Re-forge with a clearer description, or pass --force to "
            f"keep this output anyway.",
            file=sys.stderr,
        )
        return 1

    print(f"[Tool Forge] staged at:\n  {result.staged_dir}")
    print(f"  spec.yaml:        {result.spec_path}")
    print(f"  tool.py:          {result.tool_path}")
    print(f"  catalog-diff:     {result.catalog_diff_path}")
    print(f"  forge.log:        {result.log_path}")
    if result.staging_blocked and args.force:
        print()
        print(
            "[Tool Forge] WARNING: --force used. Hard flags were not "
            "addressed. REJECTED.md is alongside the staged folder."
        )
    print()
    print("Next steps (T3-T4 will automate these):")
    print(
        "  1. Review tool.py — read it, run ruff, run the generated tests "
        "if any."
    )
    print(
        "  2. Move tool.py into src/forest_soul_forge/tools/builtin/ and "
        "register it in builtin/__init__.py."
    )
    print(
        "  3. Append catalog-diff.yaml's entry to "
        "config/tool_catalog.yaml's `tools:` block."
    )
    print("  4. Restart the daemon. The new tool is dispatchable.")
    return 0


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------
def _build_provider(override: str | None) -> Any:
    """Construct a provider from DaemonSettings without standing up the
    full daemon. ``override='local'`` or ``'frontier'`` forces one
    explicitly; None uses ``settings.default_provider``."""
    from forest_soul_forge.daemon.config import build_settings
    from forest_soul_forge.daemon.providers.frontier import FrontierProvider
    from forest_soul_forge.daemon.providers.local import LocalProvider

    settings = build_settings()
    pick = (override or settings.default_provider).strip().lower()

    if pick == "local":
        return LocalProvider(
            base_url=settings.local_base_url,
            models=settings.local_model_map(),
            timeout_s=settings.local_timeout_s,
        )
    if pick == "frontier":
        if not settings.frontier_enabled:
            raise SystemExit(
                "Frontier provider is disabled by FSF_FRONTIER_ENABLED. "
                "Enable it or pass --provider local."
            )
        return FrontierProvider(
            enabled=True,
            base_url=settings.frontier_base_url,
            api_key=settings.frontier_api_key,
            models=settings.frontier_model_map(),
            timeout_s=settings.frontier_timeout_s,
        )
    raise SystemExit(
        f"unknown provider {pick!r}; expected 'local' or 'frontier'"
    )


def _resolve_operator() -> str:
    """Best-effort: $USER env var, fallback to 'operator'."""
    import os
    return os.environ.get("USER") or os.environ.get("USERNAME") or "operator"
