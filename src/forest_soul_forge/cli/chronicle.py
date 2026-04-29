"""``fsf chronicle`` — export an agent's life as HTML/Markdown.

ADR-003X K5. Three modes:

  fsf chronicle <instance_id>             # per-agent (default)
  fsf chronicle --bond <bond_name>        # per-triune
  fsf chronicle --full-chain              # whole forge

Outputs to ``data/chronicles/<name>__<date>.html`` by default; the
``--out`` flag overrides. Markdown form via ``--md``. Payload is
sanitized by default — pass ``--include-payload`` to embed full
event_data fields (operator-only; safe-to-share defaults are
metadata-only).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _resolve_chain_path(explicit: str | None) -> Path:
    """Pick the audit chain path. Order:
       1. --chain-path flag
       2. FSF_AUDIT_CHAIN_PATH env var
       3. examples/audit_chain.jsonl (default daemon path)
    """
    import os
    if explicit:
        return Path(explicit)
    env = os.environ.get("FSF_AUDIT_CHAIN_PATH")
    if env:
        return Path(env)
    # The daemon writes to examples/audit_chain.jsonl by default per the
    # current settings; fall back gracefully if it's not there.
    candidates = [
        Path("examples/audit_chain.jsonl"),
        Path("data/audit_chain.jsonl"),
    ]
    for p in candidates:
        if p.exists():
            return p
    # No file found; return the first candidate so the caller's "file
    # not found" error mentions a concrete path.
    return candidates[0]


def _load_agent_dna(instance_id: str) -> tuple[str, str]:
    """Return (short_dna, agent_name) for the given instance_id by
    consulting the registry. Raises SystemExit on lookup failure."""
    from forest_soul_forge.daemon.config import build_settings
    from forest_soul_forge.registry import Registry
    from forest_soul_forge.registry.registry import UnknownAgentError

    settings = build_settings()
    try:
        reg = Registry.bootstrap(settings.registry_db_path)
    except Exception as e:
        raise SystemExit(f"could not open registry at {settings.registry_db_path}: {e}")
    try:
        agent = reg.get_agent(instance_id)
    except UnknownAgentError:
        raise SystemExit(
            f"agent {instance_id!r} not found in registry "
            f"({settings.registry_db_path})"
        )
    return agent.dna, agent.agent_name


def run_chronicle(args: argparse.Namespace) -> int:
    from forest_soul_forge.core.audit_chain import AuditChain
    from forest_soul_forge.chronicle import (
        filter_by_bond_name,
        filter_by_dna,
        render_html,
        render_markdown,
    )

    chain_path = _resolve_chain_path(args.chain_path)
    if not chain_path.exists():
        print(
            f"audit chain not found at {chain_path}. "
            "Pass --chain-path or set FSF_AUDIT_CHAIN_PATH.",
            file=sys.stderr,
        )
        return 1

    chain = AuditChain(chain_path)
    all_entries = chain.read_all()

    # Pick scope. Mutually exclusive at the parser level; we re-validate
    # here so the function is callable from tests without argparse.
    has_inst = bool(args.instance_id)
    has_bond = bool(args.bond)
    has_full = bool(args.full_chain)
    if sum((has_inst, has_bond, has_full)) != 1:
        print(
            "fsf chronicle: pass exactly one of <instance_id>, --bond, --full-chain",
            file=sys.stderr,
        )
        return 2

    if has_inst:
        dna, agent_name = _load_agent_dna(args.instance_id)
        entries = filter_by_dna(all_entries, dna)
        title = f"Chronicle: {agent_name} ({args.instance_id})"
        subtitle = f"DNA {dna} · {len(entries)} events"
        slug = f"{agent_name.replace(' ', '_')}__{args.instance_id[:16]}"
    elif has_bond:
        entries = filter_by_bond_name(all_entries, args.bond)
        title = f"Chronicle: triune {args.bond!r}"
        subtitle = f"{len(entries)} bond-related events"
        slug = f"triune_{args.bond}"
    else:
        entries = all_entries
        title = "Chronicle: full forge"
        subtitle = f"{len(entries)} total events from {chain_path.name}"
        slug = "full_chain"

    if not entries:
        print(
            f"no entries match filter — chain has {len(all_entries)} total "
            f"events but none for this scope.",
            file=sys.stderr,
        )
        # Continue anyway and write an empty chronicle so the operator
        # sees something. Better than failing silently.

    # Render.
    if args.md:
        body = render_markdown(
            entries, title=title,
            include_payload=args.include_payload,
            sort_reverse=args.reverse,
        )
        ext = "md"
    else:
        body = render_html(
            entries, title=title, subtitle=subtitle,
            include_payload=args.include_payload,
            sort_reverse=args.reverse,
        )
        ext = "html"

    # Output path.
    if args.out:
        out_path = Path(args.out)
    else:
        date = datetime.utcnow().strftime("%Y-%m-%d")
        out_dir = Path("data/chronicles")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{slug}__{date}.{ext}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024
    print(f"✓ chronicle written: {out_path}")
    print(f"  events: {len(entries)}  ·  size: {size_kb:.1f} KB  ·  payload: "
          f"{'included' if args.include_payload else 'sanitized'}")
    return 0


def add_subparser(parent_sub: argparse._SubParsersAction) -> None:
    """Register ``fsf chronicle ...`` under the root parser."""
    chron = parent_sub.add_parser(
        "chronicle",
        help="Export an agent / triune / forge history as HTML or Markdown.",
    )
    chron.add_argument(
        "instance_id", nargs="?", default=None,
        help="Agent instance_id to render. Mutually exclusive with --bond / --full-chain.",
    )
    chron.add_argument(
        "--bond", default=None,
        help="Render a triune bond instead of an individual agent.",
    )
    chron.add_argument(
        "--full-chain", action="store_true",
        help="Render the entire forge audit chain (large for old chains).",
    )
    chron.add_argument(
        "--md", action="store_true",
        help="Output Markdown instead of HTML. Useful for git-friendly diffs.",
    )
    chron.add_argument(
        "--include-payload", action="store_true",
        help=(
            "Embed raw event_data fields. Default is sanitized one-liners "
            "only — chronicles can be shared without leaking memory contents, "
            "tool digests, or secret names. Operators only."
        ),
    )
    chron.add_argument(
        "--reverse", action="store_true",
        help="Newest events first (default: oldest first).",
    )
    chron.add_argument(
        "--out", default=None,
        help=(
            "Output path. Default: data/chronicles/<slug>__<date>.<ext>"
        ),
    )
    chron.add_argument(
        "--chain-path", default=None,
        help=(
            "Override the audit chain JSONL path. Defaults to "
            "$FSF_AUDIT_CHAIN_PATH or examples/audit_chain.jsonl."
        ),
    )
    chron.set_defaults(_run=run_chronicle)
