"""``fsf`` command root — argparse dispatch to subcommands.

Subcommand layout:

    fsf forge tool ...    → forest_soul_forge.cli.forge_tool:run
    fsf forge skill ...   → forest_soul_forge.cli.forge_skill:run (future)

Keeping the dispatch flat here so adding ``fsf agents``, ``fsf audit``,
etc. is a one-line registration in ``_build_parser``.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsf",
        description="Forest Soul Forge — local-first agent foundry CLI.",
    )
    parser.add_argument(
        "--version", action="version", version=_version_string(),
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    sub.required = True

    # `fsf forge ...` — sub-tree for ADR-0030 + ADR-0031.
    forge = sub.add_parser(
        "forge",
        help="Author new tools (ADR-0030) or skills (ADR-0031).",
    )
    forge_sub = forge.add_subparsers(dest="forge_cmd", metavar="<artifact>")
    forge_sub.required = True

    # `fsf forge tool "..."`
    forge_tool = forge_sub.add_parser(
        "tool",
        help="Forge a new tool primitive from an English description.",
    )
    forge_tool.add_argument(
        "description",
        help=(
            "Plain-English description of what the tool should do. "
            "Quote it to keep argparse happy. The Tool Forge LLM uses "
            "this to propose a ToolSpec + Python implementation."
        ),
    )
    forge_tool.add_argument(
        "--name", default=None,
        help=(
            "Override the proposed tool name. Useful when you want a "
            "specific snake_case name and don't trust the LLM to pick."
        ),
    )
    forge_tool.add_argument(
        "--version", default="1",
        help="Version string for the new tool (default: '1').",
    )
    forge_tool.add_argument(
        "--provider", default=None,
        help=(
            "Override the active provider for codegen. Format "
            "'local' or 'frontier'. Defaults to the daemon's "
            "default_provider setting."
        ),
    )
    forge_tool.add_argument(
        "--out-dir", default="data/forge/staged",
        help="Where to drop the staged forge output (default: data/forge/staged).",
    )
    forge_tool.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Stop after the propose stage. No code generated, no files "
            "written. Useful for sanity-checking the LLM's read of the "
            "description before paying for codegen tokens."
        ),
    )
    forge_tool.add_argument(
        "--no-prompt", action="store_true",
        help=(
            "Skip the y/N confirmations and proceed automatically. "
            "Intended for non-interactive scripted usage; interactive "
            "usage should leave this off."
        ),
    )
    forge_tool.set_defaults(_run=_run_forge_tool)

    # `fsf forge skill ...` — placeholder; ADR-0031 T1+ will wire this.
    forge_skill = forge_sub.add_parser(
        "skill",
        help="(ADR-0031, not yet implemented) Forge a skill manifest.",
    )
    forge_skill.add_argument("description", nargs="?", default="")
    forge_skill.set_defaults(_run=_run_forge_skill_stub)

    return parser


def _run_forge_tool(args: argparse.Namespace) -> int:
    """Hand off to forest_soul_forge.cli.forge_tool.run."""
    from forest_soul_forge.cli.forge_tool import run as forge_tool_run
    return forge_tool_run(args)


def _run_forge_skill_stub(args: argparse.Namespace) -> int:
    print(
        "fsf forge skill: ADR-0031 not yet implemented. "
        "See docs/decisions/ADR-0031-skill-forge.md for the design.",
        file=sys.stderr,
    )
    return 2


def _version_string() -> str:
    """Best-effort version pull. Avoids importing the daemon for a CLI version
    string — the package metadata is enough."""
    try:
        from importlib.metadata import version
        return f"fsf {version('forest-soul-forge')}"
    except Exception:
        return "fsf (version unknown)"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    runner = getattr(args, "_run", None)
    if runner is None:
        parser.print_help()
        return 2
    try:
        return int(runner(args) or 0)
    except KeyboardInterrupt:
        print("\n(interrupted)", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
