"""``fsf provenance ...`` — ADR-0072 T2 (B303) operator CLI.

Two subcommands surface the behavior-provenance substrate from
B290. Both read-only — no mutation to the operator-mutable
preferences.yaml or the agent-mutable learned_rules.yaml.

  - ``fsf provenance precedence`` — print the four-layer
    precedence table (hardcoded_handoff > constitutional >
    preference > learned). The operator answer to "when a
    learned rule contradicts my preference, which one wins?"

  - ``fsf provenance resolve <layer_a> <layer_b>`` — given two
    layer names, print which one wins. Same logic as
    ``resolve_precedence`` from B290; surfaced as a CLI so
    operator can sanity-check ad-hoc layer pairs without
    grepping ADRs.

  - ``fsf provenance list`` — print loaded preferences and
    learned rules side-by-side. Quick scan for "what rules are
    active right now?"

Read-only across the board. Writes (preference edits, rule
auto-pruning) land in T3-T5.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Register ``fsf provenance ...`` subcommands."""
    prov = parent_subparsers.add_parser(
        "provenance",
        help=(
            "Behavior provenance inspection (ADR-0072). Read-only "
            "precedence table + rule listing."
        ),
    )
    prov_sub = prov.add_subparsers(
        dest="provenance_cmd", metavar="<subcmd>",
    )
    prov_sub.required = True

    # ---- precedence --------------------------------------------------------
    p_prec = prov_sub.add_parser(
        "precedence",
        help=(
            "Print the four-layer precedence table. ADR-0072 D1 "
            "ordering: hardcoded_handoff > constitutional > "
            "preference > learned."
        ),
    )
    p_prec.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of formatted text.",
    )
    p_prec.set_defaults(_run=_run_precedence)

    # ---- resolve -----------------------------------------------------------
    p_res = prov_sub.add_parser(
        "resolve",
        help=(
            "Resolve a conflict between two layers. Prints which "
            "one wins under ADR-0072 D1's ordering."
        ),
    )
    p_res.add_argument(
        "layer_a",
        help="First layer (hardcoded_handoff / constitutional / preference / learned).",
    )
    p_res.add_argument(
        "layer_b",
        help="Second layer.",
    )
    p_res.set_defaults(_run=_run_resolve)

    # ---- list --------------------------------------------------------------
    p_list = prov_sub.add_parser(
        "list",
        help=(
            "List loaded preferences and learned rules. Read-only "
            "inspection of the substrate the orchestrator consults."
        ),
    )
    p_list.add_argument(
        "--preferences-path", default=None,
        help=(
            "Override preferences.yaml path. Default: the daemon's "
            "configured location (data/operator/preferences.yaml)."
        ),
    )
    p_list.add_argument(
        "--learned-rules-path", default=None,
        help=(
            "Override learned_rules.yaml path. Default: "
            "data/learned_rules.yaml."
        ),
    )
    p_list.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON.",
    )
    p_list.set_defaults(_run=_run_list)


# ---------------------------------------------------------------------------
# Subcommand runners
# ---------------------------------------------------------------------------


def _run_precedence(args: argparse.Namespace) -> int:
    """Print the precedence table."""
    from forest_soul_forge.core.behavior_provenance import PRECEDENCE

    # Sort descending by weight — operator scans top-down.
    sorted_layers = sorted(
        PRECEDENCE.items(), key=lambda kv: kv[1], reverse=True,
    )
    if getattr(args, "json", False):
        print(json.dumps(
            {layer: weight for layer, weight in sorted_layers},
            indent=2,
        ))
        return 0

    # Formatted text. Two-column table.
    print("ADR-0072 D1 — behavior precedence (highest first):")
    print()
    print("  weight  layer")
    print("  ------  -----")
    for layer, weight in sorted_layers:
        print(f"  {weight:>6}  {layer}")
    print()
    print("Conflict resolution: higher weight wins.")
    return 0


def _run_resolve(args: argparse.Namespace) -> int:
    """Print which of two layers wins under the precedence table."""
    from forest_soul_forge.core.behavior_provenance import (
        PRECEDENCE,
        resolve_precedence,
    )

    try:
        winner = resolve_precedence(args.layer_a, args.layer_b)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"winner: {winner}")
    print(
        f"  ({args.layer_a} = {PRECEDENCE[args.layer_a]}, "
        f"{args.layer_b} = {PRECEDENCE[args.layer_b]})"
    )
    return 0


def _run_list(args: argparse.Namespace) -> int:
    """List loaded preferences and learned rules side by side."""
    from pathlib import Path

    from forest_soul_forge.core.behavior_provenance import (
        BehaviorProvenanceError,
        load_learned_rules,
        load_preferences,
    )

    pref_path = (
        Path(args.preferences_path) if args.preferences_path else None
    )
    rules_path = (
        Path(args.learned_rules_path) if args.learned_rules_path else None
    )

    # Load both. A missing file isn't fatal — load_*() returns an
    # empty config so the operator can see "no preferences yet".
    try:
        prefs = load_preferences(pref_path)
    except BehaviorProvenanceError as e:
        print(f"error loading preferences: {e}", file=sys.stderr)
        return 2

    try:
        rules = load_learned_rules(rules_path)
    except BehaviorProvenanceError as e:
        print(f"error loading learned rules: {e}", file=sys.stderr)
        return 2

    def _pref_to_dict(p):
        return {
            "id": p.id,
            "domain": p.domain,
            "weight": p.weight,
            "statement": p.statement,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }

    def _rule_to_dict(r):
        return {
            "id": r.id,
            "domain": r.domain,
            "weight": r.weight,
            "statement": r.statement,
            "proposer_agent_dna": r.proposer_agent_dna,
            "created_at": r.created_at,
        }

    if getattr(args, "json", False):
        out: dict[str, Any] = {
            "preferences": [_pref_to_dict(p) for p in prefs.preferences],
            "learned_rules": {
                "pending_activation": [
                    _rule_to_dict(r) for r in rules.pending_activation
                ],
                "active": [
                    _rule_to_dict(r) for r in rules.active
                ],
            },
        }
        print(json.dumps(out, indent=2))
        return 0

    # Formatted text.
    print(f"PREFERENCES ({len(prefs.preferences)} loaded)")
    print("=" * 60)
    if not prefs.preferences:
        print("  (none)")
    else:
        for p in prefs.preferences:
            print(f"  [{p.id}] domain={p.domain} weight={p.weight}")
            print(f"    {p.statement}")
            print()

    print(
        f"LEARNED RULES — pending ({len(rules.pending_activation)}) + "
        f"active ({len(rules.active)})"
    )
    print("=" * 60)
    if rules.pending_activation:
        print("Pending Reality-Anchor verification:")
        for r in rules.pending_activation:
            print(
                f"  [{r.id}] domain={r.domain} weight={r.weight} "
                f"proposer={r.proposer_agent_dna[:12]}…"
            )
            print(f"    {r.statement}")
            print()
    if rules.active:
        print("Active:")
        for r in rules.active:
            print(
                f"  [{r.id}] domain={r.domain} weight={r.weight} "
                f"proposer={r.proposer_agent_dna[:12]}…"
            )
            print(f"    {r.statement}")
            print()
    if not (rules.pending_activation or rules.active):
        print("  (none)")
    return 0
