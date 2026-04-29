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
    forge_tool.add_argument(
        "--force", action="store_true",
        help=(
            "Stage the forged tool even if static analysis raised hard "
            "flags or generated tests failed. The folder still gets a "
            "REJECTED.md alongside; the operator is on the hook for "
            "review. Useful during forge iteration when you know the "
            "LLM nearly got it right."
        ),
    )
    forge_tool.add_argument(
        "--no-prove", action="store_true",
        help=(
            "Skip the sandboxed test run (ADR-0030 T3b). The test file "
            "is still generated and staged; it just isn't executed. "
            "Useful when running forge in environments without pytest."
        ),
    )
    forge_tool.add_argument(
        "--test-timeout", type=float, default=30.0,
        help="Per-test-run timeout in seconds (default: 30).",
    )
    forge_tool.set_defaults(_run=_run_forge_tool)

    # `fsf forge skill ...` — ADR-0031 T1 propose-only.
    forge_skill = forge_sub.add_parser(
        "skill",
        help="Forge a new skill manifest from an English workflow description.",
    )
    forge_skill.add_argument(
        "description",
        help=(
            "Plain-English description of the workflow. The LLM emits a "
            "YAML manifest (steps + data flow) which we validate before "
            "staging."
        ),
    )
    forge_skill.add_argument(
        "--name", default=None,
        help="Override the proposed skill name (snake_case).",
    )
    forge_skill.add_argument(
        "--version", default="1",
        help="Version string for the new skill (default: '1').",
    )
    forge_skill.add_argument(
        "--provider", default=None,
        help=(
            "Override the active provider for codegen ('local' or "
            "'frontier'). Defaults to settings.default_provider."
        ),
    )
    forge_skill.add_argument(
        "--out-dir", default="data/forge/skills/staged",
        help=(
            "Where to drop the staged manifest "
            "(default: data/forge/skills/staged)."
        ),
    )
    forge_skill.set_defaults(_run=_run_forge_skill)

    # `fsf install ...` — promote staged forges into the live catalog.
    install = sub.add_parser(
        "install",
        help="Install a forged tool or skill into the live catalog.",
    )
    install_sub = install.add_subparsers(dest="install_cmd", metavar="<artifact>")
    install_sub.required = True

    install_skill = install_sub.add_parser(
        "skill",
        help="Install a staged Skill Forge manifest into the live catalog.",
    )
    install_skill.add_argument(
        "staged_dir",
        help=(
            "Path to a Skill Forge staged folder (contains manifest.yaml). "
            "Typically data/forge/skills/staged/<name>.v<version>/."
        ),
    )
    install_skill.add_argument(
        "--install-dir", default=None,
        help=(
            "Override the install directory. Defaults to "
            "settings.skill_install_dir (data/forge/skills/installed/)."
        ),
    )
    install_skill.add_argument(
        "--overwrite", action="store_true",
        help="Replace the target file if it already exists.",
    )
    install_skill.add_argument(
        "--no-reload", action="store_true",
        help=(
            "Skip the POST /skills/reload after copying. The new manifest "
            "loads on next daemon boot."
        ),
    )
    install_skill.set_defaults(_run=_run_install_skill)

    install_tool = install_sub.add_parser(
        "tool",
        help=(
            "Install a staged Tool Forge tool. Defaults to plugin mode "
            "(data/plugins/, no daemon restart). --builtin opts into the "
            "legacy in-source path."
        ),
    )
    install_tool.add_argument(
        "staged_dir",
        help="Path to a Tool Forge staged folder (contains tool.py + spec.yaml).",
    )
    install_tool.add_argument(
        "--builtin", action="store_true",
        help=(
            "Legacy mode: copy tool.py to src/forest_soul_forge/tools/builtin/ "
            "and append catalog YAML. Daemon restart required. Use for "
            "in-source dev work; otherwise leave off and use the default "
            "plugin mode."
        ),
    )
    install_tool.add_argument(
        "--plugins-dir", default=None,
        help=(
            "Override the plugin directory. Defaults to "
            "settings.plugins_dir (data/plugins/). Plugin mode only."
        ),
    )
    install_tool.add_argument(
        "--builtin-dir", default=None,
        help=(
            "Override the builtin directory (--builtin mode only). "
            "Defaults to src/forest_soul_forge/tools/builtin/."
        ),
    )
    install_tool.add_argument(
        "--catalog-path", default=None,
        help=(
            "Override the catalog YAML path (--builtin mode only). "
            "Defaults to config/tool_catalog.yaml."
        ),
    )
    install_tool.add_argument(
        "--overwrite", action="store_true",
        help="Replace the target if it already exists.",
    )
    install_tool.add_argument(
        "--force", action="store_true",
        help=(
            "Install even if REJECTED.md is present (Tool Forge static-"
            "analysis or test-run failures). Use during forge iteration "
            "when you accept the risk."
        ),
    )
    install_tool.add_argument(
        "--no-reload", action="store_true",
        help=(
            "Plugin mode only: skip the POST /tools/reload after copy. "
            "The new tool loads on next daemon boot."
        ),
    )
    install_tool.set_defaults(_run=_run_install_tool)

    # `fsf triune ...` — bond peer-root agents into a triune (ADR-003X K4).
    from forest_soul_forge.cli.triune import add_subparser as _triune_add_subparser
    _triune_add_subparser(sub)

    # `fsf chronicle ...` — export agent/triune/forge history (ADR-003X K5).
    from forest_soul_forge.cli.chronicle import add_subparser as _chronicle_add_subparser
    _chronicle_add_subparser(sub)

    return parser


def _run_forge_tool(args: argparse.Namespace) -> int:
    """Hand off to forest_soul_forge.cli.forge_tool.run."""
    from forest_soul_forge.cli.forge_tool import run as forge_tool_run
    return forge_tool_run(args)


def _run_forge_skill(args: argparse.Namespace) -> int:
    """Hand off to forest_soul_forge.cli.forge_skill.run."""
    from forest_soul_forge.cli.forge_skill import run as forge_skill_run
    return forge_skill_run(args)


def _run_install_skill(args: argparse.Namespace) -> int:
    """Hand off to forest_soul_forge.cli.install.run_skill."""
    from forest_soul_forge.cli.install import run_skill
    return run_skill(args)


def _run_install_tool(args: argparse.Namespace) -> int:
    """Hand off to forest_soul_forge.cli.install.run_tool."""
    from forest_soul_forge.cli.install import run_tool
    return run_tool(args)


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
